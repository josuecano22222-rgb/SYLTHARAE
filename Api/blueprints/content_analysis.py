"""
Content Analysis API Routes

Per-file analysis from the stored content: word counts, distinct words, and
how often each category (tag) occurs - what
``database.analyzers.ContentAnalysisEngine.analyze_content`` computes.

These routes used to call ``engine.analyze_file`` / ``engine.analyze_batch``,
which the engine never had, so every request failed with a 500 (found by the
live production smoke, ANALYSIS-01). Sentiment, topic and entity extraction
were designed but never implemented: those routes answer 501 rather than
"""

import logging

from flask import Blueprint, jsonify, request

from core.errors import client_error
from database import DatabaseHub
from database.analyzers import ContentAnalysisEngine
from database.analyzers.analysis_engine import AnalysisConfig

logger = logging.getLogger(__name__)

content_analysis_bp = Blueprint('content_analysis', __name__)

#: Most files one batch request may analyse.
MAX_BATCH_FILES = AnalysisConfig().max_results


class _AnalysisFailed(Exception):
    """The engine reported an error; its text stays in the server log."""


def _file_exists(file_id: int) -> bool:
    from Api.services.original_file import OriginalFileService
    return OriginalFileService.describe(file_id).get('reason') != 'not-found'


def _analyse(file_id: int) -> dict:
    results = ContentAnalysisEngine(db_hub=DatabaseHub()).analyze_content(file_id)
    if results.get('error'):
        # The engine returns the exception text; the client must not see it.
        raise _AnalysisFailed(f"analysis of file {file_id} failed: {results['error']}")
    return results


def _not_found(file_id: int):
    return jsonify({'success': False, 'error': f'File {file_id} was not found'}), 404


def _failed(exc: Exception, what: str):
    logger.error("%s failed: %s", what, exc, exc_info=not isinstance(exc, _AnalysisFailed))
    return client_error(exc, subsystem="content_analysis",
                        public_message=f"The {what} could not be completed",
                        success_key="success")


@content_analysis_bp.route('/api/analysis/file/<int:file_id>', methods=['GET'])
def analyze_file(file_id: int):
    """Word count, distinct words, category occurrences and a preview."""
    try:
        if not _file_exists(file_id):
            return _not_found(file_id)
        return jsonify({'success': True, 'data': _analyse(file_id)})
    except Exception as e:
        return _failed(e, "file analysis")


@content_analysis_bp.route('/api/analysis/statistics/<int:file_id>', methods=['GET'])
def get_statistics(file_id: int):
    """The numeric part of the file analysis."""
    try:
        if not _file_exists(file_id):
            return _not_found(file_id)
        results = _analyse(file_id)
        categories = results.get('categories') or {}
        return jsonify({'success': True, 'data': {
            'file_id': file_id,
            'word_count': results.get('word_count', 0),
            'unique_words': results.get('unique_words', 0),
            'category_count': len(categories),
            'categories': categories,
        }})
    except Exception as e:
        return _failed(e, "statistics analysis")


@content_analysis_bp.route('/api/analysis/batch', methods=['POST'])
def analyze_batch():
    """Analyse several files: ``{"file_ids": [1, 2, 3]}``.

    Returns one result per file found, and the ids that do not exist.
    """
    data = request.get_json(silent=True) or {}
    file_ids = data.get('file_ids')
    if (not isinstance(file_ids, list) or not file_ids
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in file_ids)):
        return jsonify({'success': False,
                        'error': "'file_ids' must be a non-empty list of file ids"}), 400
    if len(file_ids) > MAX_BATCH_FILES:
        return jsonify({'success': False,
                        'error': f"At most {MAX_BATCH_FILES} files per batch"}), 400
    try:
        results, not_found = [], []
        for file_id in dict.fromkeys(file_ids):  # de-duplicated, order kept
            if _file_exists(file_id):
                results.append(_analyse(file_id))
            else:
                not_found.append(file_id)
        return jsonify({'success': True, 'data': {
            'files': results,
            'not_found': not_found,
            'total_words': sum(r.get('word_count', 0) for r in results),
        }})
    except Exception as e:
        return _failed(e, "batch analysis")


def _not_implemented(kind: str):
    return jsonify({
        'success': False,
        'code': 'not_implemented',
        'error': f'{kind} analysis is not available in this version',
    }), 501


@content_analysis_bp.route('/api/analysis/sentiment/<int:file_id>', methods=['GET'])
def get_sentiment(file_id: int):
    return _not_implemented('Sentiment')


@content_analysis_bp.route('/api/analysis/topics/<int:file_id>', methods=['GET'])
def get_topics(file_id: int):
    return _not_implemented('Topic')


@content_analysis_bp.route('/api/analysis/entities/<int:file_id>', methods=['GET'])
def get_entities(file_id: int):
    return _not_implemented('Entity')
