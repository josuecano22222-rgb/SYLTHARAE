"""
Translation API Routes
Provides centralized translation management for the frontend
"""

from flask import Blueprint, jsonify, request, current_app, render_template
from flask_babel import get_locale, _
from werkzeug.exceptions import HTTPException
import json
import logging
from pathlib import Path

from core.errors import client_error
from core.security.flask_ext import admin_required, current_user
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

logger = logging.getLogger(__name__)

translations_bp = Blueprint('translations', __name__)


def _translations_failure(exc, public_message: str):
    """A translation failure, answered the way every other failure is.

    The reader is told what could not be done and given a correlation id to
    quote; the exception text stays in the log. `str(exc)` used to be returned
    from two of these endpoints, which is how a filesystem path or a locale
    parser's message reaches a browser - the same defect the settings pipeline
    was built to stop (spec §38, §76).
    """
    original = getattr(exc, "original_exception", None)
    if isinstance(exc, HTTPException) and original is None:
        return exc
    return client_error(original or exc,
                        subsystem="translations",
                        public_message=public_message,
                        success_key="success")


def get_all_translations(locale: str = None) -> dict:
    """
    Get all translations for a given locale
    
    Args:
        locale: Language code (e.g., 'en', 'ar'). If None, uses current locale.
    
    Returns:
        Dictionary of all translations
    """
    if locale is None:
        try:
            locale = str(get_locale())
        except Exception:
            locale = 'en'
    
    translations = {}
    
    try:
        # Load translations from Babel message files
        from flask_babel import get_translations
        import babel.support
        
        # Get Flask-Babel translations for the requested locale
        with current_app.app_context():
            # Force locale if specified
            if locale:
                from flask import g
                g.locale = locale
            
            translations_obj = get_translations()
            
            if translations_obj and hasattr(translations_obj, '_catalog'):
                # Extract all message IDs and their translations
                catalog = translations_obj._catalog
                for message_id, message_string in catalog.items():
                    # Babel stores plural entries under tuple keys
                    # (``('%(n)s file', 0)``). A tuple key cannot be a JSON
                    # object key - ``jsonify`` fails while sorting the keys -
                    # and a plural form is not a flat UI string anyway.
                    if isinstance(message_id, tuple):
                        message_id = message_id[0] if message_id else None
                    if not isinstance(message_id, str):
                        continue
                    if message_id and message_id != '':
                        # Use the translated string, or fallback to message_id
                        if message_string:
                            translations[message_id] = message_string
                        else:
                            translations[message_id] = message_id
            
            # Also try to load from .po files directly
            try:
                translations_dir = Path(current_app.root_path).parent / 'translations' / locale / 'LC_MESSAGES'
                po_file = translations_dir / 'messages.po'
                
                if po_file.exists():
                    from babel.messages import catalog as babel_catalog
                    from babel.messages.pofile import read_po
                    
                    with open(po_file, 'rb') as f:
                        catalog = read_po(f, locale=locale)
                        
                        for message in catalog:
                            # Plural entries (tuple ids, used by the report
                            # narrative through ngettext) are not flat UI
                            # strings; a tuple key would break jsonify.
                            if not isinstance(message.id, str):
                                continue
                            if message.id and message.id != '':
                                if message.string:
                                    translations[message.id] = message.string
                                else:
                                    translations[message.id] = message.id
            except Exception as e:
                logger.debug(f"Could not load from .po file: {e}")
        
        # Also load from config.json if available
        config_path = Path(current_app.root_path).parent / 'config.json'
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Add any translation-related config
                    if 'translations' in config:
                        translations.update(config['translations'].get(locale, {}))
            except Exception as e:
                logger.debug(f"Could not load translations from config: {e}")
        
    except Exception as e:
        logger.error(f"Error loading translations for locale {locale}: {e}")

    # Database overrides are deliberately sparse and are merged last so an
    # administrator's saved value wins over the checked-in PO/MO default.
    try:
        from Api.services.translation_management import get_translation_overrides

        for override in get_translation_overrides(locale).values():
            translations[override['msgid']] = override['translation']
    except Exception as e:
        logger.warning("Could not load saved translation overrides for %s: %s",
                       locale, e.__class__.__name__)

    return translations


# Every page loads its UI strings from here on boot (app-i18n.js), so it is an
# interactive read like the theme: under the 600/hour default the catalog was
# refused after ~600 page views from one address (live browser smoke through
# nginx) - an office behind one NAT address gets there in an hour. See
# INTERACTIVE_READ_LIMIT.
@limiter.limit(INTERACTIVE_READ_LIMIT)
@translations_bp.route('/api/i18n/catalog', methods=['GET'])
def get_i18n_catalog():
    """Public, read-only client localization catalog.

    Returns *only* the Babel message catalog (UI strings) for the requested
    locale. Unlike /api/translations it never touches configuration files, so
    it is safe to expose to every user - the client i18n runtime
    (static/js/modules/core/app-i18n.js) needs this catalog to translate
    JavaScript-rendered interfaces for non-admin users as well.
    """
    try:
        locale = request.args.get('locale')
        if not locale:
            try:
                locale = str(get_locale())
            except Exception:
                locale = 'en'
        # Only serve locales the application actually knows about.
        known = current_app.config.get('LANGUAGES', {})
        if locale not in known:
            # Accept a base-language match (e.g. 'ar-EG' -> 'ar').
            base = str(locale).split('-')[0]
            locale = base if base in known else 'en'

        translations = {}
        try:
            # Resolve the project root by walking up from the app package
            # (root_path is <project>/apps/web) until the translations dir.
            project_root = Path(current_app.root_path)
            translations_dir = None
            for _ in range(4):
                candidate = project_root / 'translations' / locale / 'LC_MESSAGES'
                if candidate.is_dir():
                    translations_dir = candidate
                    break
                project_root = project_root.parent
            po_file = translations_dir / 'messages.po' if translations_dir else None
            if po_file and po_file.exists():
                from babel.messages.pofile import read_po
                with open(po_file, 'rb') as f:
                    catalog = read_po(f, locale=locale)
                    for message in catalog:
                        # Only flat UI strings: a plural entry has a tuple id
                        # (singular, plural) and tuple forms, is rendered on
                        # the server through ngettext, and as a JSON key would
                        # make jsonify fail - which the handler below turned
                        # into an empty catalog for every client (found when
                        # NARR-01 added the first plural entries).
                        if not isinstance(message.id, str) or not isinstance(message.string, str):
                            continue
                        if message.id and message.string:
                            translations[message.id] = message.string
        except Exception as e:
            logger.debug(f"Could not load i18n catalog for {locale}: {e}")

        try:
            from Api.services.translation_management import get_translation_overrides

            for override in get_translation_overrides(locale).values():
                translations[override['msgid']] = override['translation']
        except Exception as e:
            logger.warning("Could not merge saved client translations for %s: %s",
                           locale, e.__class__.__name__)

        return jsonify({
            'success': True,
            'locale': locale,
            'count': len(translations),
            'translations': translations
        })
    except Exception as e:
        logger.error(f"Error building i18n catalog: {e}")
        return jsonify({
            'success': False,
            'locale': 'en',
            'translations': {}
        }), 200


@translations_bp.route('/api/translations', methods=['GET'])
def get_translations():
    """API endpoint to get all translations for current locale"""
    try:
        locale = request.args.get('locale')
        if not locale:
            try:
                locale = str(get_locale())
            except Exception:
                locale = 'en'
        
        translations = get_all_translations(locale)
        
        return jsonify({
            'success': True,
            'locale': locale,
            'translations': translations
        })
    except Exception as e:
        logger.error(f"Error getting translations: {e}", exc_info=True)
        return _translations_failure(e, "The translation catalog could not be read")


@translations_bp.route('/api/translations/locale', methods=['GET'])
def get_current_locale():
    """API endpoint to get current locale"""
    try:
        locale = str(get_locale())
        return jsonify({
            'success': True,
            'locale': locale
        })
    except Exception:
        return jsonify({
            'success': True,
            'locale': 'en'
        })


@translations_bp.route('/api/translations/available', methods=['GET'])
def get_available_locales():
    """API endpoint to get list of available locales"""
    try:
        languages = current_app.config.get('LANGUAGES', {})
        return jsonify({
            'success': True,
            'locales': list(languages.keys()),
            'languages': languages
        })
    except Exception as e:
        logger.error(f"Error getting available locales: {e}", exc_info=True)
        return _translations_failure(e, "The language list could not be read")



@translations_bp.route('/translations/manage', methods=['GET'])
@admin_required
def translation_management_page():
    """Admin-only frontend for reviewing and editing screen translations."""
    from Api.services.translation_management import (
        supported_translation_languages,
        validate_translation_locale,
    )

    locale = str(get_locale() or 'en')
    if not validate_translation_locale(locale):
        locale = 'ar'
    return render_template(
        'Settings/translation_manager.html',
        translation_languages=supported_translation_languages(),
        translation_default_locale=locale,
    )


@translations_bp.route('/api/translations/manage/catalog', methods=['GET'])
@admin_required
def translation_management_catalog_api():
    """Read a filtered page of source strings and effective translations."""
    from Api.services.translation_management import (
        supported_translation_languages,
        translation_management_catalog,
        validate_translation_locale,
    )

    locale = request.args.get('locale', 'ar')
    if not validate_translation_locale(locale):
        return jsonify({'success': False, 'error': 'Choose a supported translation language.'}), 400

    try:
        page = int(request.args.get('page', '1'))
        per_page = int(request.args.get('per_page', '50'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Page values must be whole numbers.'}), 400
    if page < 1 or per_page < 10 or per_page > 100:
        return jsonify({'success': False, 'error': 'Page size must be between 10 and 100.'}), 400

    status = request.args.get('status', 'all')
    if status not in {'all', 'translated', 'missing', 'overridden'}:
        return jsonify({'success': False, 'error': 'Choose a valid translation status.'}), 400

    try:
        result = translation_management_catalog(
            locale=locale,
            query=request.args.get('q', '')[:200],
            screen=request.args.get('screen', '')[:300],
            status=status,
            page=page,
            per_page=per_page,
        )
        result['languages'] = supported_translation_languages()
        return jsonify({'success': True, **result})
    except Exception as exc:
        logger.exception("Could not read translation manager catalog")
        return _translations_failure(exc, "The translation catalog could not be read")


@translations_bp.route('/api/translations/manage/catalog', methods=['PUT'])
@admin_required
def save_translation_management_catalog_api():
    """Save a small, validated batch of locale overrides atomically."""
    from Api.services.translation_management import (
        placeholder_signature,
        save_translation_changes,
        source_entry_for_id,
        validate_translation_locale,
    )

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'success': False, 'error': 'A JSON request body is required.'}), 400
    locale = payload.get('locale')
    if not isinstance(locale, str) or not validate_translation_locale(locale):
        return jsonify({'success': False, 'error': 'Choose a supported translation language.'}), 400
    submitted = payload.get('translations')
    if not isinstance(submitted, list) or not submitted or len(submitted) > 100:
        return jsonify({'success': False, 'error': 'Submit between 1 and 100 translation changes.'}), 400

    changes = []
    seen_ids = set()
    for item in submitted:
        if not isinstance(item, dict):
            return jsonify({'success': False, 'error': 'Each translation change must be an object.'}), 400
        entry_id = item.get('id')
        if not isinstance(entry_id, str) or entry_id in seen_ids:
            return jsonify({'success': False, 'error': 'Translation ids must be unique strings.'}), 400
        seen_ids.add(entry_id)
        source = source_entry_for_id(entry_id)
        if source is None:
            return jsonify({'success': False, 'error': 'A source string is not in the current catalog.'}), 400

        reset = item.get('reset') is True
        translation = item.get('translation', '')
        if not reset:
            if not isinstance(translation, str) or not translation.strip():
                return jsonify({'success': False, 'error': 'Translations cannot be blank; use Reset to catalog instead.'}), 400
            if len(translation) > 10000:
                return jsonify({'success': False, 'error': 'A translation cannot exceed 10,000 characters.'}), 400
            if placeholder_signature(source['msgid']) != placeholder_signature(translation):
                return jsonify({
                    'success': False,
                    'error': 'Keep every named placeholder exactly as it appears in the source string.',
                    'id': entry_id,
                }), 400

        changes.append({
            'id': source['id'],
            'msgid': source['msgid'],
            'translation': translation if not reset else '',
            'reset': reset,
        })

    try:
        user = current_user()
        saved = save_translation_changes(locale, changes, getattr(user, 'id', None))
        return jsonify({'success': True, 'saved': saved, 'locale': locale})
    except Exception as exc:
        logger.exception("Could not save translation manager changes")
        return _translations_failure(exc, "The translation changes could not be saved")


def register_translation_routes(app):
    """Register translation routes with the Flask app"""
    app.register_blueprint(translations_bp)
    logger.info("✅ Translation API routes registered")

