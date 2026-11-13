"""
Advanced Search Algorithms
Implements Google-like and social media search algorithms including:
- BM25 ranking algorithm
- Query expansion with synonyms
- Fuzzy matching
- Multi-field boosting
- Recency boost
- Phrase matching
"""

import logging
import re
import math
from typing import Dict, List, Optional, Tuple, Any, Set
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date
import difflib

logger = logging.getLogger(__name__)


class BM25Ranker:
    """
    BM25 (Best Matching 25) ranking algorithm.
    A probabilistic ranking function used by search engines.
    """
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """
        Initialize BM25 ranker.
        
        Args:
            k1: Term frequency saturation parameter (default: 1.5)
            b: Length normalization parameter (default: 0.75)
        """
        self.k1 = k1
        self.b = b
        self._avg_doc_length = None
        self._doc_freqs = {}  # Document frequency for each term
        self._doc_lengths = {}  # Document length for each document
        self._total_docs = 0
    
    def fit(self, documents: List[Dict[str, Any]], text_field: str = 'text'):
        """
        Fit the BM25 model on a collection of documents.
        
        Args:
            documents: List of documents with text fields
            text_field: Field name containing the text
        """
        self._total_docs = len(documents)
        self._doc_freqs = defaultdict(int)
        self._doc_lengths = {}
        term_doc_counts = defaultdict(set)
        
        # Calculate document frequencies and lengths
        for doc_id, doc in enumerate(documents):
            text = doc.get(text_field, '')
            terms = self._tokenize(text)
            self._doc_lengths[doc_id] = len(terms)
            
            # Track which documents contain each term
            for term in set(terms):
                term_doc_counts[term].add(doc_id)
        
        # Calculate document frequencies
        for term, doc_set in term_doc_counts.items():
            self._doc_freqs[term] = len(doc_set)
        
        # Calculate average document length
        if self._total_docs > 0:
            self._avg_doc_length = sum(self._doc_lengths.values()) / self._total_docs
        else:
            self._avg_doc_length = 0
    
    def score(self, query: str, document: Dict[str, Any], text_field: str = 'text') -> float:
        """
        Calculate BM25 score for a query-document pair.
        
        Args:
            query: Search query string
            document: Document dictionary
            text_field: Field name containing the text
        
        Returns:
            BM25 score
        """
        if self._total_docs == 0 or self._avg_doc_length == 0:
            return 0.0
        
        query_terms = self._tokenize(query)
        doc_text = document.get(text_field, '')
        doc_terms = self._tokenize(doc_text)
        doc_length = len(doc_terms)
        
        # Count term frequencies in document
        term_freqs = Counter(doc_terms)
        
        score = 0.0
        for term in query_terms:
            if term not in self._doc_freqs:
                continue
            
            # Term frequency in document
            tf = term_freqs.get(term, 0)
            
            # Document frequency (how many documents contain this term)
            df = self._doc_freqs[term]
            
            # Inverse document frequency
            idf = math.log((self._total_docs - df + 0.5) / (df + 0.5))
            
            # BM25 formula
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * (doc_length / self._avg_doc_length))
            
            score += idf * (numerator / denominator)
        
        return score
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms."""
        if not text:
            return []
        # Unicode-aware tokenization that includes Hebrew (\u0590-\u05FF), Arabic (\u0600-\u06FF),
        # English (\w), and other scripts
        words = re.findall(r'[\w\u0590-\u05FF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF\'-]+', text, re.UNICODE)
        # Filter out single non-alphanumeric characters and normalize
        words = [w.lower() for w in words if len(w) > 1 or w.isalnum()]
        return words


class QueryExpander:
    """
    Query expansion using synonyms, related terms, and word variations.
    """
    
    def __init__(self):
        """Initialize query expander with synonym dictionary."""
        # Basic synonym dictionary (can be extended with external sources)
        self.synonyms = {
            'file': ['document', 'record', 'data'],
            'document': ['file', 'record', 'paper'],
            'email': ['message', 'mail', 'correspondence'],
            'message': ['email', 'mail', 'communication'],
            'report': ['document', 'summary', 'analysis'],
            'analysis': ['report', 'study', 'evaluation'],
            'data': ['information', 'content', 'details'],
            'information': ['data', 'content', 'details'],
        }
        
        # Common word variations
        self.variations = {
            'analyze': ['analysis', 'analyzing', 'analyzed'],
            'create': ['creation', 'creating', 'created'],
        }
    
    def expand(self, query: str, max_expansions: int = 3) -> List[str]:
        """
        Expand query with synonyms and related terms.
        
        Args:
            query: Original query string
            max_expansions: Maximum number of expansion terms per word
        
        Returns:
            List of expanded query terms
        """
        terms = query.lower().split()
        expanded_terms = set(terms)
        
        for term in terms:
            # Add synonyms
            if term in self.synonyms:
                expanded_terms.update(self.synonyms[term][:max_expansions])
            
            # Add variations
            if term in self.variations:
                expanded_terms.update(self.variations[term][:max_expansions])
        
        return list(expanded_terms)
    
    def get_related_terms(self, term: str) -> List[str]:
        """Get related terms for a given term."""
        related = []
        if term in self.synonyms:
            related.extend(self.synonyms[term])
        if term in self.variations:
            related.extend(self.variations[term])
        return related


class FuzzyMatcher:
    """
    Fuzzy matching for typo tolerance and approximate string matching.
    """
    
    @staticmethod
    def similarity(seq1: str, seq2: str) -> float:
        """
        Calculate similarity ratio between two strings using SequenceMatcher.
        
        Args:
            seq1: First string
            seq2: Second string
        
        Returns:
            Similarity ratio (0.0 to 1.0)
        """
        return difflib.SequenceMatcher(None, seq1.lower(), seq2.lower()).ratio()
    
    @staticmethod
    def find_matches(query: str, candidates: List[str], threshold: float = 0.6) -> List[Tuple[str, float]]:
        """
        Find fuzzy matches for a query in a list of candidates.
        
        Args:
            query: Search query
            candidates: List of candidate strings
            threshold: Minimum similarity threshold
        
        Returns:
            List of (candidate, similarity_score) tuples
        """
        matches = []
        for candidate in candidates:
            similarity = FuzzyMatcher.similarity(query, candidate)
            if similarity >= threshold:
                matches.append((candidate, similarity))
        
        # Sort by similarity (descending)
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
    
    @staticmethod
    def get_close_matches(query: str, candidates: List[str], n: int = 3, cutoff: float = 0.6) -> List[str]:
        """
        Get close matches using difflib.
        
        Args:
            query: Search query
            candidates: List of candidate strings
            n: Maximum number of matches
            cutoff: Minimum similarity cutoff
        
        Returns:
            List of close matches
        """
        return difflib.get_close_matches(query.lower(), [c.lower() for c in candidates], n=n, cutoff=cutoff)


class RelevanceScorer:
    """
    Multi-factor relevance scoring combining multiple signals.
    """
    
    def __init__(
        self,
        field_weights: Optional[Dict[str, float]] = None,
        recency_weight: float = 0.1,
        recency_half_life_days: int = 30
    ):
        """
        Initialize relevance scorer.
        
        Args:
            field_weights: Weights for different fields (e.g., {'file_name': 2.0, 'content': 1.0})
            recency_weight: Weight for recency boost (0.0 to 1.0)
            recency_half_life_days: Days for recency score to decay by half
        """
        self.field_weights = field_weights or {'file_name': 2.0, 'content': 1.0, 'path': 0.5}
        self.recency_weight = recency_weight
        self.recency_half_life_days = recency_half_life_days
    
    def calculate_relevance(
        self,
        query: str,
        document: Dict[str, Any],
        base_score: float = 0.0,
        field_scores: Optional[Dict[str, float]] = None
    ) -> float:
        """
        Calculate multi-factor relevance score.
        
        Args:
            query: Search query
            document: Document dictionary
            base_score: Base relevance score (e.g., from BM25)
            field_scores: Scores for different fields
        
        Returns:
            Combined relevance score
        """
        score = base_score
        
        # Apply field-specific boosting
        if field_scores:
            for field, field_score in field_scores.items():
                weight = self.field_weights.get(field, 1.0)
                score += field_score * weight
        
        # Apply recency boost
        recency_score = self._calculate_recency_boost(document)
        score += recency_score * self.recency_weight
        
        # Apply exact match boost
        exact_match_boost = self._calculate_exact_match_boost(query, document)
        score += exact_match_boost
        
        # Apply phrase match boost
        phrase_boost = self._calculate_phrase_boost(query, document)
        score += phrase_boost
        
        return max(0.0, score)
    
    def _calculate_recency_boost(self, document: Dict[str, Any]) -> float:
        """Calculate recency boost based on file date."""
        file_date = document.get('file_date')
        if not file_date:
            return 0.0
        
        # Parse date if string
        if isinstance(file_date, str):
            try:
                file_date = datetime.fromisoformat(file_date.replace('Z', '+00:00'))
            except:
                return 0.0
        
        # Calculate days since file date
        if isinstance(file_date, date) and not isinstance(file_date, datetime):
            file_date = datetime.combine(file_date, datetime.min.time())
        
        days_ago = (datetime.now() - file_date).days
        
        # Exponential decay: score = 2^(-days/half_life)
        if days_ago <= 0:
            return 1.0
        
        recency_score = math.pow(2, -days_ago / self.recency_half_life_days)
        return recency_score
    
    def _calculate_exact_match_boost(self, query: str, document: Dict[str, Any]) -> float:
        """Calculate boost for exact query matches in file name."""
        file_name = document.get('file_name', '').lower()
        query_lower = query.lower()
        
        # Exact match in file name
        if query_lower in file_name:
            return 10.0
        
        # All query words in file name
        query_words = query_lower.split()
        if all(word in file_name for word in query_words):
            return 5.0
        
        return 0.0
    
    def _calculate_phrase_boost(self, query: str, document: Dict[str, Any]) -> float:
        """Calculate boost for phrase matches."""
        file_name = document.get('file_name', '').lower()
        query_lower = query.lower()
        
        # Exact phrase match
        if query_lower in file_name:
            return 15.0
        
        # Phrase match in content (if available)
        content = document.get('content', '').lower()
        if query_lower in content:
            return 8.0
        
        return 0.0


class QueryUnderstanding:
    """
    Query understanding and intent detection.
    """
    
    @staticmethod
    def detect_intent(query: str) -> Dict[str, Any]:
        """
        Detect search intent from query - Google-like parsing.
        Supports: quotes for exact phrases, AND/OR/NOT operators, parentheses.
        
        Args:
            query: Search query
        
        Returns:
            Dictionary with intent information including parsed terms, phrases, operators
        """
        query_lower = query.lower()
        
        # Extract quoted phrases
        quoted_phrases = re.findall(r'"([^"]+)"', query)
        has_quotes = len(quoted_phrases) > 0
        
        # Remove quoted phrases from query for further processing
        query_without_quotes = re.sub(r'"[^"]+"', '', query)
        
        # Detect operators (AND, OR, NOT)
        has_and = bool(re.search(r'\bAND\b', query_without_quotes, re.IGNORECASE))
        has_or = bool(re.search(r'\bOR\b', query_without_quotes, re.IGNORECASE))
        has_not = bool(re.search(r'\bNOT\b', query_without_quotes, re.IGNORECASE))
        has_operators = has_and or has_or or has_not
        
        # Extract terms (split by operators, handle parentheses)
        terms = []
        exclude_terms = []
        
        if has_operators:
            # Parse with operators
            # Split by AND/OR/NOT, preserving operators
            parts = re.split(r'\s+(AND|OR|NOT)\s+', query_without_quotes, flags=re.IGNORECASE)
            
            current_operator = 'AND'  # Default is AND
            for part in parts:
                part = part.strip()
                if part.upper() in ['AND', 'OR', 'NOT']:
                    current_operator = part.upper()
                elif part:
                    if current_operator == 'NOT':
                        exclude_terms.append(part)
                    else:
                        terms.append({'term': part, 'operator': current_operator})
        else:
            # Simple space-separated terms (default AND)
            terms = [{'term': t.strip(), 'operator': 'AND'} 
                    for t in query_without_quotes.split() if t.strip()]
        
        # Detect file type queries
        file_types = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt', '.csv', '.json', '.xml', '.jpg', '.png']
        query_type = 'general'
        if any(ft in query_lower for ft in file_types):
            query_type = 'file_type'
        
        # Detect date queries
        date_patterns = [
            r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
            r'\d{2}/\d{2}/\d{4}',   # MM/DD/YYYY
            r'\d{4}',               # Year
        ]
        if any(re.search(pattern, query) for pattern in date_patterns):
            query_type = 'date'
        
        intent = {
            'type': query_type,
            'has_phrase': len(quoted_phrases) > 0,
            'has_quotes': has_quotes,
            'has_operators': has_operators,
            'phrases': quoted_phrases,
            'terms': terms,
            'exclude_terms': exclude_terms,
            'original_query': query
        }
        
        return intent
    
    @staticmethod
    def extract_entities(query: str) -> Dict[str, List[str]]:
        """
        Extract entities from query (dates, file types, etc.).
        
        Args:
            query: Search query
        
        Returns:
            Dictionary of entity types and values
        """
        entities = {
            'dates': [],
            'file_types': [],
            'numbers': []
        }
        
        # Extract dates
        date_patterns = [
            (r'\d{4}-\d{2}-\d{2}', '%Y-%m-%d'),
            (r'\d{2}/\d{2}/\d{4}', '%m/%d/%Y'),
        ]
        for pattern, fmt in date_patterns:
            matches = re.findall(pattern, query)
            entities['dates'].extend(matches)
        
        # Extract file types
        file_types = ['.pdf', '.doc', '.xls', '.txt', '.csv', '.json', '.xml', '.jpg', '.png']
        for ft in file_types:
            if ft in query.lower():
                entities['file_types'].append(ft)
        
        # Extract numbers
        numbers = re.findall(r'\b\d+\b', query)
        entities['numbers'] = numbers
        
        return entities


class SearchRanker:
    """
    Combined search ranking system using multiple algorithms.
    """
    
    def __init__(self):
        """Initialize search ranker with all components."""
        self.bm25 = BM25Ranker()
        self.query_expander = QueryExpander()
        self.fuzzy_matcher = FuzzyMatcher()
        self.relevance_scorer = RelevanceScorer()
        self.query_understanding = QueryUnderstanding()
    
    def rank_documents(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        use_expansion: bool = True,
        use_fuzzy: bool = True
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Rank documents for a query using multiple algorithms.
        
        Args:
            query: Search query
            documents: List of document dictionaries
            use_expansion: Whether to use query expansion
            use_fuzzy: Whether to use fuzzy matching
        
        Returns:
            List of (document, score) tuples sorted by score (descending)
        """
        if not documents:
            return []
        
        # Understand query intent
        intent = self.query_understanding.detect_intent(query)
        
        # Expand query if enabled
        expanded_query = query
        if use_expansion and not intent['has_quotes']:
            expanded_terms = self.query_expander.expand(query)
            expanded_query = ' '.join(expanded_terms)
        
        # Fit BM25 on documents
        self.bm25.fit(documents, text_field='combined_text')
        
        # Score each document
        scored_documents = []
        for doc in documents:
            # Create combined text field for BM25
            combined_text = ' '.join([
                doc.get('file_name', ''),
                doc.get('file_path', ''),
                doc.get('content', '')[:1000]  # Limit content length
            ])
            doc['combined_text'] = combined_text
            
            # Calculate BM25 score
            bm25_score = self.bm25.score(query, doc, text_field='combined_text')
            
            # Calculate field-specific scores
            field_scores = {
                'file_name': self._score_field(query, doc.get('file_name', '')),
                'content': self._score_field(query, doc.get('content', '')[:1000])
            }
            
            # Calculate final relevance score
            relevance_score = self.relevance_scorer.calculate_relevance(
                query=query,
                document=doc,
                base_score=bm25_score,
                field_scores=field_scores
            )
            
            scored_documents.append((doc, relevance_score))
        
        # Sort by score (descending)
        scored_documents.sort(key=lambda x: x[1], reverse=True)
        
        return scored_documents
    
    def _score_field(self, query: str, text: str) -> float:
        """Calculate simple term frequency score for a field."""
        if not text:
            return 0.0
        
        text_lower = text.lower()
        query_lower = query.lower()
        query_terms = query_lower.split()
        
        score = 0.0
        for term in query_terms:
            # Count occurrences
            count = text_lower.count(term)
            score += count
        
        return score

