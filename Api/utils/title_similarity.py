"""
Title Similarity Detection Utilities
Groups identical and similar titles together
"""

import logging
from typing import List, Dict, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def _calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate similarity ratio between two texts.
    Returns a value between 0.0 (completely different) and 1.0 (identical).
    
    Uses SequenceMatcher which is good for detecting similar strings.
    Internal function used by group_similar_titles and find_similar_titles.
    """
    if not text1 or not text2:
        return 0.0
    
    # Normalize texts: lowercase and strip
    text1_norm = text1.lower().strip()
    text2_norm = text2.lower().strip()
    
    # If identical after normalization, return 1.0
    if text1_norm == text2_norm:
        return 1.0
    
    # Use SequenceMatcher for similarity
    similarity = SequenceMatcher(None, text1_norm, text2_norm).ratio()
    return similarity


def group_similar_titles(titles: List[Dict], similarity_threshold: float = 0.8) -> List[Dict]:
    """
    Group titles by similarity.
    
    Args:
        titles: List of title dictionaries with 'id', 'name', etc.
        similarity_threshold: Minimum similarity ratio to group titles (0.0-1.0)
    
    Returns:
        List of grouped titles with similarity information
    """
    if not titles:
        return []
    
    # Create groups
    groups = []
    processed = set()
    
    for i, title in enumerate(titles):
        if i in processed:
            continue
        
        title_text = title.get('name', '').strip()
        if not title_text:
            continue
        
        # Start a new group with this title
        group = {
            'group_id': len(groups) + 1,
            'representative_title': title_text,
            'titles': [title],
            'count': 1,
            'is_identical': False
        }
        
        # Find similar titles
        for j, other_title in enumerate(titles[i+1:], start=i+1):
            if j in processed:
                continue
            
            other_text = other_title.get('name', '').strip()
            if not other_text:
                continue
            
            similarity = _calculate_similarity(title_text, other_text)
            
            if similarity >= similarity_threshold:
                group['titles'].append(other_title)
                group['count'] += 1
                processed.add(j)
                
                # If similarity is 1.0, mark as identical
                if similarity == 1.0:
                    group['is_identical'] = True
        
        groups.append(group)
        processed.add(i)
    
    return groups


def find_similar_titles(target_title: str, all_titles: List[Dict], 
                       similarity_threshold: float = 0.7, max_results: int = 10) -> List[Dict]:
    """
    Find titles similar to a target title.
    
    Args:
        target_title: The title to find similarities for
        all_titles: List of all title dictionaries
        similarity_threshold: Minimum similarity ratio
        max_results: Maximum number of results to return
    
    Returns:
        List of similar titles with similarity scores, sorted by similarity
    """
    if not target_title or not all_titles:
        return []
    
    similar = []
    
    for title in all_titles:
        title_text = title.get('name', '').strip()
        if not title_text:
            continue
        
        similarity = _calculate_similarity(target_title, title_text)
        
        if similarity >= similarity_threshold:
            similar.append({
                **title,
                'similarity_score': similarity,
                'similarity_percent': int(similarity * 100)
            })
    
    # Sort by similarity (highest first)
    similar.sort(key=lambda x: x['similarity_score'], reverse=True)
    
    return similar[:max_results]

