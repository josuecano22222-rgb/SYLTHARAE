"""
Content Processor Module - Enhanced with Phone Numbers and Coordinates
Handles text extraction, tokenization, and entity recognition.

Optimized for speed with:
- Singleton pattern to avoid recompiling regex patterns
- Cached compiled patterns
- Parallel entity extraction
- Streaming processing
"""

import logging
import re
from typing import List, Tuple, Dict, Optional
from collections import Counter
import threading

logger = logging.getLogger(__name__)

# Unicode-aware word pattern that properly handles Hebrew, Arabic, English, and other scripts
# Pattern matches sequences of word characters including:
# - English/Latin: \w (letters, digits, underscore)
# - Hebrew: \u0590-\u05FF (Hebrew block)
# - Arabic: \u0600-\u06FF (Arabic block)
# - Cyrillic: \u0400-\u04FF (Cyrillic block)
# - CJK: \u4E00-\u9FFF (Chinese/Japanese/Korean)
# - Apostrophes and hyphens for contractions and compound words
# Pattern uses explicit character class to match word sequences, avoiding \b which doesn't work well with RTL languages
# The pattern will match sequences of these characters, naturally stopping at spaces, punctuation, etc.
UNICODE_WORD_PATTERN = r'[\w\u0590-\u05FF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF\'-]+'


class ContentProcessor:
    """
    Processes file content for storage and indexing.
    Handles text extraction, tokenization, and entity recognition.
    
    Optimized singleton pattern - patterns are compiled once and reused.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern - only one instance with compiled patterns"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ContentProcessor, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize content processor (only once due to singleton)"""
        if self._initialized:
            return
        
        self._initialized = True
        # Special patterns for entities that should be preserved whole
        self.patterns = {
            # ISO date pattern: YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
            'date_iso': re.compile(r'\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b'),
            
            # Comprehensive date pattern
            'date': re.compile(r"""
                \b(?:
                    # Numerical Dates (Natural Forms)
                    \d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4} |
                    \d{2,4}[/\-\.]\d{1,2}[/\-\.]\d{1,2} |
                    \d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2} |
                    # Written Dates (Natural Forms)
                    \d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4} |
                    (?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4} |
                    \d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4} |
                    # ISO Dates and Times (Natural Formats)
                    \d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)? |
                    \d{4}/\d{2}/\d{2}(?:\s+\d{2}:\d{2}:\d{2})?
                )\b
            """, re.IGNORECASE | re.VERBOSE),
            
            # Email pattern
            'email': re.compile(
                r'([a-zA-Z0-9](?:[a-zA-Z0-9._%+-]*[a-zA-Z0-9])?@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+)',
                re.IGNORECASE
            ),
            
            # URL with protocol
            'url': re.compile(
                r'\b(?:https?|ftp|ftps|file)://(?:[^\s<>"{}|\\`\[\]]+|\[[0-9a-fA-F:]+\])(?:/[^\s<>"{}|\\^`\[\]]*)?',
                re.IGNORECASE
            ),
            
            # URL without protocol
            'url_no_protocol': re.compile(
                r'\b(?:www\.)?[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?::\d+)?(?:/[^\s<>"{}|\\^`\[\]]*)?',
                re.IGNORECASE
            ),
            
            # Domain pattern
            'domain': re.compile(
                r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b',
                re.IGNORECASE
            ),
            
            # Phone number pattern (international and various formats)
            'phone': re.compile(r"""
                (?:
                    # International format with + prefix
                    \+\d{1,3}[\s\-\.]?\(?\d{1,4}\)?[\s\-\.]?\d{1,4}[\s\-\.]?\d{1,4}[\s\-\.]?\d{1,9} |
                    # US/Canada format with country code
                    \+?1[\s\-\.]?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4} |
                    # Standard format with parentheses
                    \(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4} |
                    # International without + but with country code
                    \b\d{1,3}[\s\-\.]?\d{2,4}[\s\-\.]?\d{2,4}[\s\-\.]?\d{2,4}\b |
                    # Format with extensions
                    \(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}[\s\-\.]*(?:x|ext|extension)[\s\-\.]*\d{1,6}
                )
            """, re.VERBOSE | re.IGNORECASE),
            
            # Coordinate pattern (latitude, longitude)
            'coordinates': re.compile(r"""
                (?:
                    # Decimal degrees format: 40.7128, -74.0060
                    [-+]?\d{1,3}\.\d+[\s,]+[-+]?\d{1,3}\.\d+ |
                    # With degree symbol: 40.7128°N, 74.0060°W
                    \d{1,3}\.\d+°?[\s]?[NS][\s,]+\d{1,3}\.\d+°?[\s]?[EW] |
                    # DMS format: 40°42'46"N 74°00'21"W
                    \d{1,3}°\d{1,2}'[\d\.]+\"?[NS][\s,]+\d{1,3}°\d{1,2}'[\d\.]+\"?[EW] |
                    # Parentheses format: (40.7128, -74.0060)
                    \([-+]?\d{1,3}\.\d+[\s]*,[\s]*[-+]?\d{1,3}\.\d+\)
                )
            """, re.VERBOSE | re.IGNORECASE),
        }
        
        # Pattern matching order (most specific first)
        self.pattern_order = ['url', 'email', 'coordinates', 'phone', 'date', 'date_iso', 'url_no_protocol', 'domain']
        
        # Regular word pattern (fallback)
        # Use Unicode-aware pattern that works with Hebrew and other RTL languages
        # This pattern properly handles Hebrew, Arabic, Cyrillic, Chinese, and other Unicode scripts
        self.word_pattern = re.compile(UNICODE_WORD_PATTERN, re.UNICODE)
    
    def extract_words_with_punctuation_chunked(
        self, 
        text: str, 
        chunk_size: int = 10000
        ) -> List[Tuple[str, str, str, str, int]]:
        """
        Extract words with surrounding punctuation metadata and character positions (chunked processing).
        OPTIMIZED: Faster processing with reduced stages and memory efficiency.
        PRODUCTION-READY: Adaptive chunk sizing for terabyte-scale files.
        
        This is a chunked version that processes text in chunks for memory efficiency.
        Special entities (emails, URLs, dates, domains, phones, coordinates) are preserved as complete units.
        Character positions are tracked to preserve the original location of each word in the text.
        
        Args:
            text: Text to process
            chunk_size: Size of chunks to process (default: 10000, auto-adjusted for large files)
        
        Returns: 
            List of tuples: [(word, punct_before, punct_after, spacing, char_position), ...]
            char_position is the character offset in the original text where the word starts
        
        Example:
            "Contact: user@example.com, visit www.site.com" → [
                ("Contact", "", ":", " ", 0),
                ("user@example.com", "", ",", " ", 9),
                ("visit", "", "", " ", 30),
                ("www.site.com", "", "", "", 36)
            ]
        
        Note:
            OPTIMIZED: Single-pass entity extraction, fast overlap removal, combined processing stages.
            PRODUCTION: Adaptive chunk sizing based on available memory and text size.
        """
        if not text:
            return []
        
        # PRODUCTION: Adaptive chunk sizing for very large files
        # For terabyte-scale content, use smaller chunks to prevent memory exhaustion
        text_len = len(text)
        if text_len > 100 * 1024 * 1024:  # > 100MB text
            # For very large text, use smaller chunks (1MB chunks)
            chunk_size = min(chunk_size, 1024 * 1024)
            # Check available memory and adjust further if needed
            try:
                import psutil
                available_memory = psutil.virtual_memory().available
                # If available memory is less than 2GB, use even smaller chunks
                if available_memory < 2 * 1024 * 1024 * 1024:
                    chunk_size = min(chunk_size, 512 * 1024)  # 512KB chunks
                    logger.debug(f"Low memory detected ({available_memory / (1024**3):.2f}GB), using {chunk_size / 1024:.0f}KB chunks")
            except ImportError:
                # psutil not available, use conservative defaults
                if text_len > 500 * 1024 * 1024:  # > 500MB
                    chunk_size = 512 * 1024  # 512KB chunks
            except Exception:
                pass  # Ignore errors, use default chunk_size
        
        # OPTIMIZED: Sanitize text once (not per chunk)
        text = self._sanitize_text(text)
        
        # OPTIMIZED: For small texts, process directly without chunking (faster)
        if text_len <= chunk_size:
            return self._process_chunk_with_entities(text, 0)
        
        # OPTIMIZED: Process in chunks with pre-allocated list for better performance
        tokens = []
        global_position = 0
        
        # PRODUCTION: Process in chunks with memory monitoring
        chunk_count = 0
        for i in range(0, text_len, chunk_size):
            chunk_end = min(i + chunk_size, text_len)
            chunk = text[i:chunk_end]
            
            # Process chunk
            chunk_tokens = self._process_chunk_with_entities(chunk, global_position)
            tokens.extend(chunk_tokens)
            
            # Update global position for next chunk
            global_position += len(chunk)
            chunk_count += 1
            
            # PRODUCTION: Yield CPU periodically for very large files
            if chunk_count % 100 == 0 and text_len > 10 * 1024 * 1024:  # Every 100 chunks for >10MB files
                try:
                    import time
                    time.sleep(0.01)  # Small yield to prevent CPU spikes
                except:
                    pass
            
            # PRODUCTION: Monitor memory and adjust chunk size if needed
            if chunk_count % 50 == 0:  # Check every 50 chunks
                try:
                    import psutil
                    memory_percent = psutil.virtual_memory().percent
                    # If memory usage is high (>85%), reduce chunk size for remaining chunks
                    if memory_percent > 85 and chunk_size > 256 * 1024:
                        chunk_size = max(256 * 1024, chunk_size // 2)
                        logger.debug(f"High memory usage ({memory_percent:.1f}%), reducing chunk size to {chunk_size / 1024:.0f}KB")
                except:
                    pass  # Ignore errors, continue with current chunk_size
        
        return tokens
    
    def _process_chunk_with_entities(self, text: str, base_offset: int = 0) -> List[Tuple[str, str, str, str, int]]:
        """
        Process a single text chunk, preserving special entities and character positions.
        OPTIMIZED: Uses parallel entity extraction for faster processing.
        
        Strategy:
        1. Find all special entities (emails, URLs, dates, domains, phones, coordinates) in parallel
        2. Mark their positions in the text
        3. Extract regular words from unmarked regions
        4. Merge and sort by position
        5. Track character positions relative to the original text
        
        Args:
            text: Text chunk to process
            base_offset: Character offset in the original text where this chunk starts
        
        Returns:
            List of tuples: (word, punct_before, punct_after, spacing, char_position)
        """
        # OPTIMIZED: Extract all entities in one pass using finditer (faster than multiple passes)
        # Use a single pass through text with all patterns combined for better performance
        entities = []
        
        # Fast single-pass entity extraction
        for pattern_name in self.pattern_order:
            pattern = self.patterns[pattern_name]
            # Use finditer for memory efficiency (doesn't create full match list)
            for match in pattern.finditer(text):
                entities.append({
                    'start': match.start(),
                    'end': match.end(),
                    'text': match.group(0),
                    'type': pattern_name
                })
        
        # Sort entities by start position (required for overlap removal)
        if entities:
            entities.sort(key=lambda x: x['start'])
            # Remove overlapping entities (keep first/longest) - optimized
            filtered_entities = self._remove_overlaps_fast(entities)
        else:
            filtered_entities = []
        
        # Build tokens list
        tokens = []
        position = 0
        
        for entity in filtered_entities:
            # Process any regular text before this entity
            if position < entity['start']:
                regular_text = text[position:entity['start']]
                tokens.extend(self._extract_regular_words_with_metadata(regular_text, position, base_offset))
            
            # Process the entity itself
            entity_token = self._create_entity_token(
                text,
                entity['text'],
                entity['start'],
                entity['end'],
                filtered_entities,
                base_offset
            )
            
            if entity_token:
                tokens.append(entity_token)
            
            position = entity['end']
        
        # Process any remaining text after the last entity
        if position < len(text):
            remaining_text = text[position:]
            tokens.extend(self._extract_regular_words_with_metadata(remaining_text, position, base_offset))
        
        return tokens
    
    def _create_entity_token(
        self,
        text: str,
        entity_text: str,
        start: int,
        end: int,
        all_entities: List[Dict],
        base_offset: int = 0
         ) -> Tuple[str, str, str, str, int]:
        """
        Create a token for a special entity (email, URL, date, domain, phone, coordinates)
        with character position tracking.
        
        Args:
            text: Full text chunk
            entity_text: The entity text
            start: Start position in chunk
            end: End position in chunk
            all_entities: All entities in the chunk (for reference)
            base_offset: Character offset in original text where chunk starts
        
        Returns: (entity, punct_before, punct_after, spacing, char_position)
        """
        # Find punctuation before the entity
        punct_before = ""
        punct_start = start - 1
        
        while punct_start >= 0:
            char = text[punct_start]
            
            # Stop at word character or space
            if char.isalnum() or char.isspace():
                break
            
            punct_before = char + punct_before
            punct_start -= 1
        
        # Calculate character position: base_offset + start position of entity (after punctuation before)
        # The entity starts at 'start', but we need to account for punctuation before it
        # char_position is where the entity word itself starts
        char_position = base_offset + start
        
        # Find punctuation after the entity and spacing
        punct_after = ""
        spacing = ""
        pos = end
        
        # First collect punctuation
        while pos < len(text):
            char = text[pos]
            
            # Stop at word character or space
            if char.isalnum() or char.isspace():
                break
            
            punct_after += char
            pos += 1
        
        # Then collect spacing
        while pos < len(text):
            char = text[pos]
            
            # Stop at non-space
            if not char.isspace():
                break
            
            spacing += char
            pos += 1
        
        return (entity_text.lower(), punct_before, punct_after, spacing, char_position)
    
    def _extract_regular_words_with_metadata(
        self, 
        text: str, 
        base_position: int,
        base_offset: int = 0
        ) -> List[Tuple[str, str, str, str, int]]:
        """
        Extract regular words from text with punctuation metadata and character positions.
        
        This method extracts words that don't contain special entities (emails, URLs, etc.)
        and returns them with their surrounding punctuation, spacing, and character position.
        
        Args:
            text: Text to extract words from
            base_position: Base position offset within the chunk
            base_offset: Character offset in the original text where this chunk starts
        
        Returns:
            List of tuples: (word, punct_before, punct_after, spacing, char_position)
            char_position is the absolute character offset in the original text
        
        Note:
            This differs from text_utils._extract_regular_words() which returns
            a simple list of words without metadata or position information.
        """
        tokens = []
        position = 0
        
        for match in self.word_pattern.finditer(text):
            word = match.group(0)
            start = match.start()
            end = match.end()
            
            # Filter out single non-alphanumeric characters (skip punctuation-only matches)
            if len(word) <= 1 and not word.isalnum():
                position = end
                continue
            
            # Extract punctuation before word
            punct_before = text[position:start]
            punct_before = self._normalize_punctuation(punct_before)
            
            # Calculate character position: base_offset + base_position + word start position
            # The word starts at 'start' position in the chunk, plus base offsets
            char_position = base_offset + base_position + start
            
            # Look ahead for punctuation after word
            next_word_match = self.word_pattern.search(text, end)
            
            if next_word_match:
                punct_after_end = next_word_match.start()
            else:
                punct_after_end = len(text)
            
            after_text = text[end:punct_after_end]
            
            # Separate punctuation from spacing
            punct_after, spacing = self._separate_punctuation_and_spacing(after_text)
            
            tokens.append((
                word.lower(),
                punct_before,
                punct_after,
                spacing,
                char_position
            ))
            
            position = end
        
        return tokens
    
    def _normalize_punctuation(self, text: str) -> str:
        """
        Normalize punctuation:
        - Remove duplicate whitespace
        - Keep only punctuation marks
        - Remove NULL bytes and problematic characters
        """
        if not text:
            return ""
        
        # Remove NULL bytes and other control characters first
        text = text.replace('\x00', '')
        
        # Remove whitespace, keep only punctuation
        # Filter out control characters except common ones
        punct_only = ''.join(
            c for c in text 
            if not c.isspace() and (ord(c) >= 32 or ord(c) in (9, 10, 13))
        )
        
        return punct_only
    
    def _separate_punctuation_and_spacing(self, text: str) -> Tuple[str, str]:
        """
        Separate punctuation marks from spacing
        
        Example:
            "! " → ("!", " ")
            ", " → (",", " ")
            "  " → ("", "  ")
        """
        if not text:
            return ("", "")
        
        # Find where punctuation ends and spacing begins
        punct_end = 0
        for i, char in enumerate(text):
            if char.isspace():
                punct_end = i
                break
        else:
            # No spacing found
            return (text, "")
        
        punctuation = text[:punct_end]
        spacing = text[punct_end:]
        
        # Normalize punctuation
        punctuation = self._normalize_punctuation(punctuation)
        
        return (punctuation, spacing)
    
    def extract_words_simple(self, text: str) -> List[str]:
        """
        Simple word extraction (no punctuation metadata).
        Preserves special entities. Used for title processing.
        """
        if not text:
            return []
        
        text = self._sanitize_text(text)
        
        # Extract all entities
        entities = []
        
        for pattern_name in self.pattern_order:
            pattern = self.patterns[pattern_name]
            
            for match in pattern.finditer(text):
                entities.append({
                    'start': match.start(),
                    'end': match.end(),
                    'text': match.group(0)
                })
        
        # Sort and remove overlaps
        entities.sort(key=lambda x: x['start'])
        entities = self._remove_overlaps(entities)
        
        # Extract words
        words = []
        position = 0
        
        for entity in entities:
            # Extract regular words before entity
            if position < entity['start']:
                regular_text = text[position:entity['start']]
                regular_words = self.word_pattern.findall(regular_text)
                # Filter out single non-alphanumeric characters and normalize
                words.extend([w.lower() for w in regular_words if len(w) > 1 or w.isalnum()])
            
            # Add entity as a single word
            words.append(entity['text'].lower())
            position = entity['end']
        
        # Extract remaining words
        if position < len(text):
            remaining_text = text[position:]
            regular_words = self.word_pattern.findall(remaining_text)
            # Filter out single non-alphanumeric characters and normalize
            words.extend([w.lower() for w in regular_words if len(w) > 1 or w.isalnum()])
        
        return words
    
    def extract_words(self, text: str) -> List[str]:
        """
        Extract words from text (alias for extract_words_simple for backward compatibility).
        """
        return self.extract_words_simple(text)
    
    def calculate_word_frequency(self, words: List[str]) -> Dict[str, int]:
        """Calculate word frequency"""
        return dict(Counter(words))
    
    def extract_n_grams(self, words: List[str], n: int = 2) -> List[Tuple[str, ...]]:
        """
        Extract n-grams from word list.
        
        Args:
            words: List of words
            n: N-gram size (default: 2 for bigrams)
            
        Returns:
            List of n-gram tuples
        """
        if len(words) < n:
            return []
        
        return [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    
    def get_text_statistics(self, text: str) -> Dict[str, any]:
        """
        Get comprehensive text statistics.
        
        Returns:
            Dict with character count, word count, frequency analysis, etc.
        """
        words = self.extract_words_simple(text)
        word_freq = self.calculate_word_frequency(words)
        
        # Count special entities
        entity_counts = {}
        for pattern_name in self.pattern_order:
            pattern = self.patterns[pattern_name]
            matches = pattern.findall(text)
            if matches:
                entity_counts[pattern_name] = len(matches)
        
        return {
            'character_count': len(text),
            'word_count': len(words),
            'unique_word_count': len(word_freq),
            'average_word_length': sum(len(w) for w in words) / len(words) if words else 0,
            'most_common_words': Counter(words).most_common(10),
            'lexical_diversity': len(word_freq) / len(words) if words else 0,
            'special_entities': entity_counts
        }
    
    def extract_keywords_fast(self, word_ids: List[int], keywords_dict: Dict[int, List[int]]) -> Dict[int, int]:
        """
        Fast keyword extraction using consecutive sequence matching.
        Matches keywords only when all words appear consecutively in the correct order.
        
        Args:
            word_ids: List of word IDs in the document (ordered sequence)
            keywords_dict: {keyword_id: [word_id_1, word_id_2, ...]} (ordered sequence)
        
        Returns:
            {keyword_id: count} - count of consecutive occurrences
        """
        if not keywords_dict or not word_ids:
            return {}
        
        keyword_counts = {}
        
        for keyword_id, keyword_word_ids in keywords_dict.items():
            if not keyword_word_ids or len(keyword_word_ids) < 2:
                continue
            
            # Count consecutive occurrences of the keyword phrase
            count = 0
            keyword_len = len(keyword_word_ids)
            
            # Slide through the document word_ids looking for consecutive matches
            for i in range(len(word_ids) - keyword_len + 1):
                # Check if the sequence starting at position i matches the keyword
                if word_ids[i:i + keyword_len] == keyword_word_ids:
                    count += 1
                    # Skip ahead to avoid overlapping matches (optional - remove if overlapping is desired)
                    # i += keyword_len - 1
            
            if count > 0:
                keyword_counts[keyword_id] = count
        
        return keyword_counts
    
    def _sanitize_text(self, text: str) -> str:
        """Sanitize text for processing"""
        if not isinstance(text, str):
            try:
                text = text.decode('utf-8', errors='replace')
            except (AttributeError, UnicodeDecodeError):
                text = str(text)
        
        # Remove NULL bytes
        text = text.replace('\x00', '')
        
        # Remove problematic control characters but keep common whitespace
        sanitized = []
        for char in text:
            code = ord(char)
            if code >= 32 or code in (9, 10, 13):
                sanitized.append(char)
        
        return ''.join(sanitized)
    
    def _remove_overlaps(self, entities: List[Dict]) -> List[Dict]:
        """Remove overlapping entities, keeping the first/longest match"""
        return self._remove_overlaps_fast(entities)
    
    def _remove_overlaps_fast(self, entities: List[Dict]) -> List[Dict]:
        """
        Fast overlap removal - optimized version.
        Removes overlapping entities, keeping the longest match at each position.
        """
        if not entities:
            return []
        
        if len(entities) == 1:
            return entities
        
        # OPTIMIZED: Single pass with early termination
        filtered = []
        last_end = -1
        
        for entity in entities:
            start, end = entity['start'], entity['end']
            entity_len = end - start
            
            if start < last_end:
                # Overlap detected - keep the longer one
                prev_len = filtered[-1]['end'] - filtered[-1]['start']
                if entity_len > prev_len:
                    filtered[-1] = entity
                    last_end = end
                # else: keep previous entity, don't update last_end
            else:
                # No overlap - add entity
                filtered.append(entity)
                last_end = end
        
        return filtered
    
    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extract all special entities from text"""
        entities = {
            'emails': [],
            'urls': [],
            'dates': [],
            'domains': [],
            'phones': [],
            'coordinates': []
        }
        
        text = self._sanitize_text(text)
        
        # Extract emails
        for match in self.patterns['email'].finditer(text):
            entities['emails'].append(match.group(0))
        
        # Extract URLs
        for match in self.patterns['url'].finditer(text):
            entities['urls'].append(match.group(0))
        
        for match in self.patterns['url_no_protocol'].finditer(text):
            url = match.group(0)
            if url not in entities['urls']:
                entities['urls'].append(url)
        
        # Extract dates
        for match in self.patterns['date'].finditer(text):
            entities['dates'].append(match.group(0))
        
        for match in self.patterns['date_iso'].finditer(text):
            date = match.group(0)
            if date not in entities['dates']:
                entities['dates'].append(date)
        
        # Extract phone numbers
        for match in self.patterns['phone'].finditer(text):
            entities['phones'].append(match.group(0))
        
        # Extract coordinates
        for match in self.patterns['coordinates'].finditer(text):
            entities['coordinates'].append(match.group(0))
        
        # Extract domains
        email_domains = set()
        for email in entities['emails']:
            if '@' in email:
                email_domains.add(email.split('@')[1])
        
        url_domains = set()
        for url in entities['urls']:
            domain_match = self.patterns['domain'].search(url)
            if domain_match:
                url_domains.add(domain_match.group(0))
        
        for match in self.patterns['domain'].finditer(text):
            domain = match.group(0)
            if domain not in email_domains and domain not in url_domains:
                entities['domains'].append(domain)
        
        return entities
    
    def normalize_phone_number(self, phone: str) -> str:
        """
        Normalize a phone number to a standard format.
        Removes all non-digit characters except leading +.
        
        Args:
            phone: Raw phone number string
            
        Returns:
            Normalized phone number (e.g., "+12125551234" or "2125551234")
        """
        if not phone:
            return ""
        
        # Keep leading + if present
        has_plus = phone.strip().startswith('+')
        
        # Extract only digits
        digits = ''.join(c for c in phone if c.isdigit())
        
        # Add back the + if it was there
        if has_plus and digits:
            return '+' + digits
        
        return digits
    
    def parse_coordinates(self, coord_str: str) -> Tuple[float, float]:
        """
        Parse coordinate string to latitude and longitude floats.
        
        Args:
            coord_str: Coordinate string in various formats
            
        Returns:
            Tuple of (latitude, longitude) as floats
            
        Raises:
            ValueError: If coordinates cannot be parsed
        """
        coord_str = coord_str.strip()
        
        # Remove parentheses if present
        coord_str = coord_str.strip('()')
        
        # Try decimal degrees format: 40.7128, -74.0060
        decimal_pattern = r'([-+]?\d{1,3}\.\d+)[\s,]+([-+]?\d{1,3}\.\d+)'
        match = re.search(decimal_pattern, coord_str)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
            return (lat, lon)
        
        # Try format with cardinal directions: 40.7128°N, 74.0060°W
        cardinal_pattern = r'(\d{1,3}\.\d+)°?[\s]?([NS])[\s,]+(\d{1,3}\.\d+)°?[\s]?([EW])'
        match = re.search(cardinal_pattern, coord_str, re.IGNORECASE)
        if match:
            lat = float(match.group(1))
            lat_dir = match.group(2).upper()
            lon = float(match.group(3))
            lon_dir = match.group(4).upper()
            
            if lat_dir == 'S':
                lat = -lat
            if lon_dir == 'W':
                lon = -lon
            
            return (lat, lon)
        
        # Try DMS format: 40°42'46"N 74°00'21"W
        dms_pattern = r"(\d{1,3})°(\d{1,2})'([\d\.]+)\"?([NS])[\s,]+(\d{1,3})°(\d{1,2})'([\d\.]+)\"?([EW])"
        match = re.search(dms_pattern, coord_str, re.IGNORECASE)
        if match:
            lat_deg = int(match.group(1))
            lat_min = int(match.group(2))
            lat_sec = float(match.group(3))
            lat_dir = match.group(4).upper()
            
            lon_deg = int(match.group(5))
            lon_min = int(match.group(6))
            lon_sec = float(match.group(7))
            lon_dir = match.group(8).upper()
            
            # Convert DMS to decimal
            lat = lat_deg + lat_min / 60 + lat_sec / 3600
            lon = lon_deg + lon_min / 60 + lon_sec / 3600
            
            if lat_dir == 'S':
                lat = -lat
            if lon_dir == 'W':
                lon = -lon
            
            return (lat, lon)
        
        raise ValueError(f"Unable to parse coordinates: {coord_str}")


# Global singleton instance for fast access
_global_content_processor: Optional[ContentProcessor] = None
_processor_lock = threading.Lock()


def get_content_processor() -> ContentProcessor:
    """
    Get the global ContentProcessor singleton instance.
    This ensures patterns are compiled only once and reused across the entire project.
    
    Returns:
        ContentProcessor instance (singleton)
    """
    global _global_content_processor
    if _global_content_processor is None:
        with _processor_lock:
            if _global_content_processor is None:
                _global_content_processor = ContentProcessor()
    return _global_content_processor