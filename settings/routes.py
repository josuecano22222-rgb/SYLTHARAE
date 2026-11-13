"""
Settings API Routes
File: settings/routes.py

RESTful API for settings management
Enhanced with all existing system features
"""

from flask import Blueprint, request, jsonify, current_app, session, g
from functools import wraps
from pathlib import Path
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
import logging
import os
import uuid
import re

from core.errors import client_error, new_correlation_id, sanitize_message
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter
from .settings_manager import get_settings_manager
from .database_validation import DatabaseConfigRejected

logger = logging.getLogger(__name__)

# Create blueprint
settings_bp = Blueprint('settings_api', __name__, url_prefix='/api/settings')


def _is_admin() -> bool:
    """True when the signed-in user is an administrator (anonymous: no)."""
    user = getattr(g, "user", None)
    return bool(getattr(user, "is_authenticated", False)
                and getattr(user, "role", None) == "admin")


def _settings_failure(exc):
    """Turn any settings failure into a response, in one place.

    Three kinds of failure reach here and each is answered according to who
    the text was written for:

    * ``HTTPException`` - Flask's own abort (404/405/...): returned untouched
      so the status and the error page stay what the framework intends.
    * ``DatabaseConfigRejected`` - a configuration the operator just
      submitted. Its message is built in ``database_validation.py`` from a
      fixed vocabulary (no host, user, password or driver text), so it is
      shown, as a 422, with a correlation id. This is the branch that used to
      be five separate ``except`` blocks; having it here means a future
      endpoint cannot forget to keep the operator's explanation.
    * anything else - logged in full server-side, answered with a client-safe
      message and a correlation id. The exception text is never returned: it
      can carry SQL, filesystem paths, connection strings and credentials.
    """
    original = getattr(exc, "original_exception", None)
    if isinstance(exc, HTTPException) and original is None:
        return exc
    candidate = original or exc
    if isinstance(candidate, DatabaseConfigRejected):
        return jsonify({
            "success": False,
            "error": sanitize_message(str(candidate)),
            "correlation_id": new_correlation_id(),
        }), 422
    return client_error(
        candidate,
        subsystem="settings",
        public_message="The settings request could not be completed",
        success_key="success",
    )


@settings_bp.errorhandler(Exception)
def _settings_unhandled(exc):
    """Blueprint-level net: no settings endpoint can bypass the error model.

    ``@handle_errors`` already covers every decorated view, but a new endpoint
    that forgets the decorator must not become a hole in the rule the security
    documentation states. Anything raised out of a settings view - including
    from a ``before_request`` or a helper the view called - is answered the
    same way.
    """
    return _settings_failure(exc)


def handle_errors(f):
    """Decorator for consistent, sanitized error handling.

    SEC-08 / the central error model: an unexpected failure is logged in full
    server-side and answered with a client-safe message plus a correlation id.
    Deliberate, user-facing rejections (a configuration the operator just
    submitted, a value they just typed) keep their explanation - see
    ``_settings_failure``.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except HTTPException:
            raise  # Flask's own aborts (404/405/...) keep their normal handling
        except Exception as e:  # noqa: BLE001 - every failure is sanitized
            return _settings_failure(e)
    return decorated_function


# ============================================================================
# GET ENDPOINTS - Read settings
# ============================================================================

@settings_bp.route('/', methods=['GET'])
@handle_errors
def get_all_settings():
    """Get all settings"""
    manager = get_settings_manager()
    
    return jsonify({
        'success': True,
        'settings': manager.export()
    })


#: Keys of the ``system`` category that are an operator's business: how the
#: process logs, and the diagnostics an administrator switches on. The shell
#: reads none of them, so a reader is not given them. This is what lets
#: ``/api/settings/system`` be readable by every signed-in role
#: (core/security/flask_ext.py) without publishing configuration.
_SYSTEM_OPERATOR_KEYS = frozenset({
    "log_level", "log_file", "debug_mode", "screen_inspector",
    "action_logging_enabled",
})


# Every page reads its shell settings from here on load (system-settings.js),
# so it is an interactive read exactly like /theme: under the 60/minute default
# an ordinary browse - or an office behind one NAT address - was refused.
@limiter.limit(INTERACTIVE_READ_LIMIT)
@settings_bp.route('/<category>', methods=['GET'])
@handle_errors
def get_category_settings(category):
    """Get settings for a specific category"""
    manager = get_settings_manager()
    settings = manager.settings
    
    # Get category object
    category_obj = getattr(settings, category, None)
    if category_obj is None:
        return jsonify({
            'success': False,
            'error': f'Unknown category: {category}'
        }), 404
    
    # Convert to dict
    if hasattr(category_obj, 'to_dict'):
        data = category_obj.to_dict()
    else:
        data = vars(category_obj)

    if category == 'system' and not _is_admin():
        # A reader gets what renders the page, not how the process is run.
        data = {key: value for key, value in data.items()
                if key not in _SYSTEM_OPERATOR_KEYS}

    return jsonify({
        'success': True,
        'category': category,
        'settings': data
    })


@settings_bp.route('/<category>/<key>', methods=['GET'])
@handle_errors
def get_single_setting(category, key):
    """Get a single setting value"""
    manager = get_settings_manager()
    full_key = f"{category}.{key}"
    value = manager.get(full_key)
    
    return jsonify({
        'success': True,
        'key': full_key,
        'value': value
    })


# ============================================================================
# POST/PUT ENDPOINTS - Update settings
# ============================================================================

@settings_bp.route('/<category>/<key>', methods=['POST', 'PUT'])
@handle_errors
def update_single_setting(category, key):
    """Update a single setting"""
    data = request.get_json() or {}
    value = data.get('value')
    
    if value is None:
        return jsonify({
            'success': False,
            'error': 'Missing "value" in request body'
        }), 400
    
    manager = get_settings_manager()
    full_key = f"{category}.{key}"

    # OPS-06: refuse to mutate database configuration here.
    #
    # ``manager.set("database.host", ...)`` followed by ``manager.save()``
    # writes an unvalidated, untested database target straight to disk -
    # reintroducing the exact outage OPS-02/03/04/05 closed. This generic
    # endpoint has no notion of validating a connection, so database settings
    # must go through ``POST /api/settings/database``, which does.
    # (The guard is here rather than in SettingsManager.set() because
    # core/initialization.py legitimately seeds database.* from config.json
    # during startup, and must keep working.)
    if category == 'database':
        return jsonify({
            'success': False,
            'error': 'Database configuration cannot be changed through this endpoint. '
                     'Use /api/settings/database, which validates and tests the '
                     'connection before saving.',
        }), 422

    # Special handling for language changes
    if category == 'system' and key == 'language':
        # Validate language code
        from flask import current_app
        languages = current_app.config.get('LANGUAGES', {})
        if value not in languages:
            return jsonify({
                'success': False,
                'error': f'Invalid language code: {value}'
            }), 400
        
        # Update app config
        current_app.config['BABEL_DEFAULT_LOCALE'] = value
        
        # Update session
        session['language'] = value
        session.permanent = True
        g._language_cookie = value  # the explicit choice must win over an older cookie
    
    # Update setting
    success, error = manager.set(full_key, value, validate=True)
    
    if not success:
        return jsonify({
            'success': False,
            'error': error
        }), 400
    
    # Save to file
    manager.save()
    
    return jsonify({
        'success': True,
        'key': full_key,
        'value': value,
        'message': f'Setting {full_key} updated successfully'
    })


@settings_bp.route('/batch', methods=['POST', 'PUT'])
@handle_errors
def batch_update_settings():
    """
    Update multiple settings at once.
    
    Request body:
    {
        "updates": {
            "system.language": "fr",
            "theme.primary_color": "#ff0000",
            "display.compact_view": true
        }
    }
    """
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({
            'success': False,
            'error': 'Invalid request body.'
        }), 400

    updates = data.get('updates', {})
    if not updates or not isinstance(updates, dict):
        return jsonify({
            'success': False,
            'error': 'No updates provided'
        }), 400

    # OPS-06: same guard as the single-setting endpoint. A batch payload may
    # contain "database.host" and friends, which would otherwise be written
    # unvalidated by ``update_many`` + ``save`` and reproduce the OPS-02
    # outage. Reject the whole batch rather than partially applying it, so a
    # mixed payload cannot half-succeed.
    db_keys = sorted(
        k for k in updates
        if isinstance(k, str) and (k == 'database' or k.startswith('database.'))
    )
    if db_keys:
        return jsonify({
            'success': False,
            'error': 'Database configuration cannot be changed through this endpoint. '
                     'Use /api/settings/database, which validates and tests the '
                     'connection before saving.',
            'rejected_keys': db_keys,
        }), 422

    manager = get_settings_manager()
    
    # Handle language changes
    if 'system.language' in updates:
        from flask import current_app
        value = updates['system.language']
        languages = current_app.config.get('LANGUAGES', {})
        if value not in languages:
            return jsonify({
                'success': False,
                'error': f'Invalid language code: {value}'
            }), 400
        current_app.config['BABEL_DEFAULT_LOCALE'] = value
        session['language'] = value
        session.permanent = True
        g._language_cookie = value  # the explicit choice must win over an older cookie
    
    # Update all settings
    success, errors = manager.update_many(updates, validate=True)
    
    if not success:
        return jsonify({
            'success': False,
            'error': 'Some updates failed',
            'errors': errors
        }), 400
    
    # Save to file
    manager.save()
    
    return jsonify({
        'success': True,
        'updated_count': len(updates),
        'message': f'Successfully updated {len(updates)} settings'
    })


# ============================================================================
# DATABASE SETTINGS ENDPOINTS
# ============================================================================

@settings_bp.route('/database', methods=['GET'])
@handle_errors
def get_database_settings():
    """Get database settings (password not exposed)"""
    manager = get_settings_manager()
    db_config = manager.settings.database
    
    return jsonify({
        'success': True,
        'settings': db_config.to_dict()  # This excludes password
    })


@settings_bp.route('/database', methods=['POST'])
@handle_errors
def update_database_settings():
    """Update database settings.

    OPS-02: this endpoint writes the configuration the application itself uses
    to authenticate, so the workflow is

        validate syntax -> test connectivity -> persist -> activate

    and **not** persist-then-hope. The live configuration object and the
    ``DB_*`` environment variables are left completely untouched until a real
    connection using the proposed values has succeeded. A rejected submission
    therefore leaves the running application exactly as it was, and an
    administrator can simply correct the values and retry.
    """
    from .database_validation import build_database_candidate, test_database_connection

    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({
            'success': False,
            'error': 'Invalid request body.',
        }), 400

    manager = get_settings_manager()

    # ``manager.settings.database`` is the LIVE configuration object. It is
    # only read here - every mutation happens after validation has passed.
    db_config = manager.settings.database

    # ---- Step 1: validate syntax / ranges (no side effects) --------------
    candidate, errors = build_database_candidate(data, db_config)
    if errors:
        return jsonify({
            'success': False,
            'error': 'The submitted database configuration is not valid.',
            'errors': errors,
        }), 422

    # ---- Step 2: prove the proposed configuration actually works ---------
    # Nothing has been modified at this point, so a failure here is
    # inherently non-destructive.
    connected, failure_message = test_database_connection(candidate)
    if not connected:
        return jsonify({
            'success': False,
            'error': failure_message,
            'message': 'Unable to connect to the database. Your existing configuration was not changed.',
        }), 422

    # ---- Step 3: commit --------------------------------------------------
    # Steps 1 and 2 above produce the structured 400/422 responses the UI
    # relies on, so they stay here; the COMMIT itself is delegated to
    # SettingsManager.apply_database_config(), the single supported mutator for
    # database configuration. Previously this endpoint mutated the live
    # DatabaseConfig with setattr and maintained its own parallel copy of the
    # commit/rollback logic - a third composition site that bypassed the
    # manager boundary. ``require_test=False`` because the probe has already
    # run above; structural validation is re-run inside and is free.
    applied, commit_error = manager.apply_database_config(
        candidate, require_test=False, activate=False
    )
    if not applied:
        return jsonify({
            'success': False,
            'error': commit_error or 'The configuration could not be saved. Your existing configuration was not changed.',
        }), 500

    db_config = manager.settings.database

    # ---- Step 4: activate ------------------------------------------------
    # Only reached once the new configuration is proven and persisted.
    #
    # Activation failure is deliberately non-fatal: by this point disk,
    # environment and the live settings object all hold the same *validated*
    # configuration, so the operation has already succeeded. Activation only
    # drops cached connections built against the previous target; if it fails,
    # the worst case is a stale cache that self-heals (the health probe re-runs
    # SELECT 1, and the storage hub is rebuilt lazily). Rolling back here would
    # discard a known-good configuration because of a cache-clearing error,
    # which is the wrong trade-off. The response tells the operator instead.
    activation_warning = None
    try:
        from .config import invalidate_database_connections
        invalidate_database_connections()
    except Exception as exc:
        activation_warning = (
            "Configuration saved, but cached database connections could not be "
            "cleared. A restart is recommended so every component picks up the "
            "new configuration."
        )
        logger.warning(
            "Database settings saved but connection invalidation failed "
            "(host=%s port=%s): %s",
            db_config.host, db_config.port, type(exc).__name__,
        )

    logger.info(
        "Database settings updated (host=%s port=%s database=%s user=%s)",
        db_config.host, db_config.port, db_config.database, db_config.user,
    )

    payload = {
        'success': True,
        'message': 'Connection successful. Configuration saved.',
        'settings': db_config.to_dict()   # excludes the password
    }
    if activation_warning:
        payload['warning'] = activation_warning
    return jsonify(payload)


# ============================================================================
# STORAGE SETTINGS ENDPOINTS
# ============================================================================

@settings_bp.route('/storage', methods=['GET'])
@handle_errors
def get_storage_settings():
    """Get storage settings"""
    manager = get_settings_manager()
    storage_config = manager.settings.storage
    
    return jsonify({
        'success': True,
        'settings': storage_config.to_dict()
    })


@settings_bp.route('/storage/<key>', methods=['POST'])
@handle_errors
def update_storage_setting(key):
    """Update a storage setting"""
    data = request.get_json() or {}
    value = data.get('value')
    
    if value is None:
        return jsonify({
            'success': False,
            'error': 'Missing "value" in request body'
        }), 400
    
    manager = get_settings_manager()
    full_key = f"storage.{key}"
    
    success, error = manager.set(full_key, value, validate=True)
    
    if not success:
        return jsonify({
            'success': False,
            'error': error
        }), 400
    
    manager.save()
    
    return jsonify({
        'success': True,
        'key': full_key,
        'value': value,
        'message': f'Storage setting {key} updated successfully'
    })


# ============================================================================
# LOGO UPLOAD/REMOVE ENDPOINTS
# ============================================================================

@settings_bp.route('/logo/upload', methods=['POST'])
@handle_errors
def upload_logo():
    """Upload app logo"""
    if 'logo' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Validate file type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico'}
    filename = secure_filename(file.filename)
    file_ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    
    if file_ext not in allowed_extensions:
        return jsonify({
            'success': False,
            'error': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'
        }), 400
    
    # Check file size (max 5MB)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    max_size = 5 * 1024 * 1024  # 5MB
    if file_size > max_size:
        return jsonify({
            'success': False,
            'error': f'File too large. Maximum size: 5MB'
        }), 400
    
    # Create logos directory
    static_folder = Path(current_app.static_folder)
    logos_dir = static_folder / 'logos'
    logos_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename
    unique_filename = f"logo_{uuid.uuid4().hex[:8]}.{file_ext}"
    filepath = logos_dir / unique_filename
    
    # Save file
    file.save(str(filepath))
    
    # Generate URL path
    logo_url = f"/static/logos/{unique_filename}"
    
    # Update setting
    manager = get_settings_manager()
    manager.set('system.app_logo', logo_url)
    manager.set('system.app_icon', '')  # Clear icon if logo is set
    manager.save()
    
    logger.info(f"✅ Logo uploaded: {logo_url}")
    
    return jsonify({
        'success': True,
        'logo_url': logo_url,
        'message': 'Logo uploaded successfully'
    })


@settings_bp.route('/logo/remove', methods=['POST'])
@handle_errors
def remove_logo():
    """Remove app logo"""
    manager = get_settings_manager()
    current_logo = manager.get('system.app_logo', '')
    
    # Remove logo file if it exists
    if current_logo:
        try:
            logo_path = Path(current_app.static_folder) / current_logo.replace('/static/', '')
            if logo_path.exists():
                logo_path.unlink()
        except Exception as e:
            logger.warning(f"Could not delete logo file: {e}")
    
    # Clear logo setting
    manager.set('system.app_logo', '')
    manager.set('system.app_icon', 'bi-file-earmark-text')  # Restore default icon
    manager.save()
    
    logger.info("✅ Logo removed")
    
    return jsonify({
        'success': True,
        'message': 'Logo removed successfully'
    })


# ============================================================================
# INTERFACE MANAGEMENT ENDPOINTS
# ============================================================================

@settings_bp.route('/interfaces', methods=['GET'])
@handle_errors
def get_interfaces():
    """Every interface the product declares, with its current state.

    The list comes from the registry, so it can no longer describe a page that
    does not exist or miss a page that does - which is what the previous
    metadata table did (``analytics`` pointed at an endpoint that was never
    registered, ``page_tips`` described no page at all, and two entries claimed
    the same route).
    """
    from .settings_adapter import get_interface_manager

    manager = get_interface_manager()
    state = manager.get_state()
    return jsonify({
        'success': True,
        'interfaces': {row['interface_id']: row for row in state.interfaces_with_state()},
        'summary': state.summary(),
    })


@settings_bp.route('/interfaces/<interface_id>', methods=['POST'])
@handle_errors
def toggle_interface(interface_id):
    """Switch one interface on or off.

    A configuration that cannot work is refused with the reason and the way
    out - disabling something another interface needs, or enabling something
    whose dependency is off. The message names the interfaces involved; it is
    the operator's own submitted configuration coming back, not a system fault.
    """
    from .settings_adapter import get_interface_manager

    data = request.get_json() or {}
    enabled = bool(data.get('enabled', True))

    manager = get_interface_manager()
    ok, message = manager.set_interface_enabled(interface_id, enabled)
    if not ok:
        return jsonify({
            'success': False,
            'error': message,
            'interface_id': interface_id,
        }), 409

    state = manager.get_state()
    entry = manager.get_all_interfaces().get(interface_id, {})
    return jsonify({
        'success': True,
        'interface_id': interface_id,
        'enabled': enabled,
        'state': entry,
        'dependents': [d.interface_id for d in manager.get_dependents(interface_id)],
        'message': f"{entry.get('name', interface_id)} {'enabled' if enabled else 'disabled'}",
    })


@settings_bp.route('/interfaces/reset', methods=['POST'])
@handle_errors
def reset_interfaces():
    """Reset every interface to the default the registry defines."""
    from .settings_adapter import get_interface_manager

    manager = get_interface_manager()
    defaults = manager.reset_interfaces_to_defaults()
    return jsonify({
        'success': True,
        'message': 'Interface settings reset to their registry defaults',
        'defaults': defaults,
    })


# ============================================================================
# THEME ENDPOINTS
# ============================================================================

# Every page reads its theme from here on load (theme-manager.js), so it is an
# interactive read: under the 60/minute default a quick browse - or an office
# sharing one address behind a NAT - was refused (seen in the live browser
# smoke through nginx). See INTERACTIVE_READ_LIMIT.
@limiter.limit(INTERACTIVE_READ_LIMIT)
@settings_bp.route('/theme', methods=['GET'])
@handle_errors
def get_theme_settings():
    """Get all theme settings"""
    manager = get_settings_manager()
    theme = manager.settings.theme
    
    return jsonify({
        'success': True,
        'theme': theme.to_dict()
    })


@settings_bp.route('/theme/<key>', methods=['POST'])
@handle_errors
def update_theme_setting(key):
    """Update a theme setting"""
    data = request.get_json() or {}
    value = data.get('value')
    
    if value is None:
        return jsonify({
            'success': False,
            'error': 'Missing "value" in request body'
        }), 400
    
    # Validate color format for color settings
    color_pattern = re.compile(r'^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$')
    css_value_pattern = re.compile(r'^[\d.]+(rem|px|em|%|deg|vh|vw|pt)$|^\d+$')
    
    non_color_settings = [
        'gradient_direction', 'spacing_xs', 'spacing_sm', 'spacing_md', 'spacing_lg',
        'spacing_xl', 'spacing_2xl', 'sidebar_width', 'font_size_xs', 'font_size_sm',
        'font_size_base', 'font_size_lg', 'font_size_xl', 'font_size_2xl', 'font_size_3xl',
        'font_weight_normal', 'font_weight_medium', 'font_weight_semibold', 'font_weight_bold',
        'border_radius_sm', 'border_radius_md', 'border_radius_lg', 'border_radius_xl',
        'shadow_sm', 'shadow_md', 'shadow_lg', 'shadow_xl',
        'icon_size_sm', 'icon_size_md', 'icon_size_lg', 'icon_size_xl', 'custom_css'
    ]
    
    if key in non_color_settings:
        # Validate CSS value
        if not (css_value_pattern.match(value) or value.endswith('deg') or 
                value.startswith('rgba') or value.startswith('rgb') or key == 'custom_css'):
            return jsonify({
                'success': False,
                'error': f'Invalid format for {key}. Must be a valid CSS value'
            }), 400
    else:
        # Validate color
        if not (color_pattern.match(value) or value.startswith('rgb(') or 
                value.startswith('rgba(')):
            return jsonify({
                'success': False,
                'error': f'Invalid color format for {key}. Must be hex, rgb(), or rgba()'
            }), 400
    
    manager = get_settings_manager()
    full_key = f"theme.{key}"
    
    success, error = manager.set(full_key, value, validate=False)  # Custom validation above
    
    if not success:
        return jsonify({
            'success': False,
            'error': error
        }), 400
    
    manager.save()
    
    return jsonify({
        'success': True,
        'key': full_key,
        'value': value,
        'message': f'Theme setting {key} updated successfully'
    })


@settings_bp.route('/theme/bulk', methods=['POST'])
@handle_errors
def update_theme_bulk():
    """Update multiple theme settings at once"""
    data = request.get_json() or {}
    colors = data.get('colors', {})
    
    if not colors:
        return jsonify({
            'success': False,
            'error': 'Colors object is required'
        }), 400
    
    manager = get_settings_manager()
    updates = {}
    
    for key, value in colors.items():
        updates[f"theme.{key}"] = value
    
    success, errors = manager.update_many(updates, validate=False)
    
    if not success:
        return jsonify({
            'success': False,
            'error': 'Some updates failed',
            'errors': errors
        }), 400
    
    manager.save()
    
    return jsonify({
        'success': True,
        'updated_count': len(updates),
        'message': f'Successfully updated {len(updates)} theme settings'
    })


@settings_bp.route('/theme/custom_css', methods=['GET', 'POST'])
@handle_errors
def theme_custom_css():
    """Get or update custom CSS"""
    manager = get_settings_manager()
    
    if request.method == 'GET':
        custom_css = manager.get('theme.custom_css', '')
        return jsonify({
            'success': True,
            'custom_css': custom_css
        })
    
    # POST
    data = request.get_json() or {}
    custom_css = data.get('custom_css', '')
    
    if not isinstance(custom_css, str):
        return jsonify({
            'success': False,
            'error': 'custom_css must be a string'
        }), 400
    
    # Size limit
    if len(custom_css) > 100000:
        return jsonify({
            'success': False,
            'error': 'custom_css is too large (max 100000 characters)'
        }), 400
    
    # Basic safety
    custom_css = custom_css.replace('</style>', '')
    
    manager.set('theme.custom_css', custom_css)
    manager.save()
    
    return jsonify({
        'success': True,
        'message': 'Custom CSS updated successfully'
    })


# ============================================================================
# IMPORT/EXPORT ENDPOINTS
# ============================================================================

@settings_bp.route('/export', methods=['GET'])
@handle_errors
def export_settings():
    """Export all settings as JSON"""
    manager = get_settings_manager()
    
    from datetime import datetime
    filename = f"settings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    # Returned as JSON (the page saves it as a file), so there is no
    # attachment header: force the disclosure record explicitly.
    from core.security.disclosure import note_disclosure
    note_disclosure(kind='settings_export', scope='settings:all', unit='settings_document',
                    row_count=1, format='json', filename=filename, force=True)
    return jsonify({
        'success': True,
        'filename': filename,
        'settings': manager.export()
    })


@settings_bp.route('/import', methods=['POST'])
@handle_errors
def import_settings():
    """
    Import settings from JSON.
    
    Request body: Complete settings dictionary
    """
    data = request.get_json(silent=True)
    if data is None:
        data = {}

    if not isinstance(data, dict):
        return jsonify({
            'success': False,
            'error': 'Invalid request body.'
        }), 400

    if not data:
        return jsonify({
            'success': False,
            'error': 'No settings data provided'
        }), 400

    manager = get_settings_manager()

    # OPS-03: importing settings replaces the whole document, including the
    # database block. It must pass the same validate -> connectivity-test gate
    # as POST /api/settings/database, or it is just a second door to the same
    # outage. Rejections from that gate arrive as DatabaseConfigRejected and
    # leave every piece of state untouched.
    try:
        success, error = manager.import_settings(data)
    except DatabaseConfigRejected as exc:
        return jsonify({
            'success': False,
            'error': str(exc),
            'message': 'The database configuration in these settings could not be used. '
                       'Your existing configuration was not changed.',
        }), 422

    if not success:
        return jsonify({
            'success': False,
            'error': error
        }), 400

    return jsonify({
        'success': True,
        'message': 'Settings imported successfully'
    })


# ============================================================================
# BACKUP/RESTORE ENDPOINTS
# ============================================================================

@settings_bp.route('/backups', methods=['GET'])
@handle_errors
def list_backups():
    """List all available backups"""
    manager = get_settings_manager()
    backups = manager.list_backups()
    
    return jsonify({
        'success': True,
        'count': len(backups),
        'backups': backups
    })


@settings_bp.route('/backups', methods=['POST'])
@handle_errors
def create_backup():
    """Create a manual backup"""
    manager = get_settings_manager()
    manager._create_backup()
    
    return jsonify({
        'success': True,
        'message': 'Backup created successfully'
    })


@settings_bp.route('/backups/<filename>/restore', methods=['POST'])
@handle_errors
def restore_backup(filename):
    """Restore settings from a backup.

    OPS-04: restoring a backup goes through ``import_settings`` and therefore
    replaces the database block too. It is subject to the same
    validate -> connectivity-test gate as every other database mutation; a
    backup pointing at an unreachable database is refused rather than
    persisted.
    """
    manager = get_settings_manager()

    # ``filename`` comes from the URL path. Reject anything that is not a
    # plain filename so it cannot be used to traverse out of the backups
    # directory (Flask already does not decode %2f, but this makes the
    # invariant explicit and independent of the WSGI layer).
    if not filename or filename != secure_filename(filename):
        return jsonify({
            'success': False,
            'error': 'Invalid backup filename.'
        }), 400

    try:
        success, error = manager.restore_backup(filename)
    except DatabaseConfigRejected as exc:
        return jsonify({
            'success': False,
            'error': str(exc),
            'message': 'The database configuration in this backup could not be used. '
                       'Your existing configuration was not changed.',
        }), 422

    if not success:
        return jsonify({
            'success': False,
            'error': error
        }), 400

    return jsonify({
        'success': True,
        'message': f'Settings restored from {filename}'
    })


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@settings_bp.route('/reload', methods=['POST'])
@handle_errors
def reload_settings():
    """Reload settings from file"""
    manager = get_settings_manager()
    success = manager.reload()
    
    if not success:
        return jsonify({
            'success': False,
            'error': 'Failed to reload settings'
        }), 500
    
    return jsonify({
        'success': True,
        'message': 'Settings reloaded successfully',
        'settings': manager.export()
    })


@settings_bp.route('/reset', methods=['POST'])
@handle_errors
def reset_to_defaults():
    """Reset all settings to default values"""
    # Require confirmation
    data = request.get_json() or {}
    confirm = data.get('confirm', False)
    
    if not confirm:
        return jsonify({
            'success': False,
            'error': 'Reset requires confirmation. Send {"confirm": true}'
        }), 400
    
    manager = get_settings_manager()
    success = manager.reset_to_defaults()
    
    if not success:
        return jsonify({
            'success': False,
            'error': 'Failed to reset settings'
        }), 500
    
    return jsonify({
        'success': True,
        'message': 'Settings reset to defaults'
    })


@settings_bp.route('/validate', methods=['POST'])
@handle_errors
def validate_settings():
    """
    Validate settings without saving.
    
    Request body: Same as batch update
    """
    data = request.get_json() or {}
    updates = data.get('updates', {})
    
    if not updates:
        return jsonify({
            'success': False,
            'error': 'No updates provided'
        }), 400
    
    manager = get_settings_manager()
    
    # Validate without saving
    from .settings_models import get_setting_definition, ValidationError
    errors = []
    
    for key, value in updates.items():
        definition = get_setting_definition(key)
        if definition:
            try:
                definition.validate(value)
            except ValidationError as e:
                errors.append(f"{key}: {str(e)}")
    
    if errors:
        return jsonify({
            'success': False,
            'errors': errors
        }), 400
    
    return jsonify({
        'success': True,
        'message': 'All settings are valid'
    })


# ============================================================================
# PAGE ROUTE
# ============================================================================

@settings_bp.route('/page', methods=['GET'])
@handle_errors
def settings_page():
    """Settings Page"""
    from flask import render_template
    from .settings_adapter import get_interface_manager
    
    try:
        from core.app_version import get_version
    except ImportError:
        def get_version():
            return "3.0.0"
    
    interface_manager = get_interface_manager()
    from core.security import ROLE_ADMIN
    from core.security.flask_ext import current_user
    can_manage_places = getattr(current_user(), "role", None) == ROLE_ADMIN
    interfaces_by_category = interface_manager.get_interfaces_by_domain()
    all_interfaces = interface_manager.get_all_interfaces()
    interface_state = interface_manager.get_state()

    return render_template(
        'Settings/settings.html',
        interfaces_by_category=interfaces_by_category,
        all_interfaces=all_interfaces,
        interface_summary=interface_state.summary(),
        interface_state_report=interface_state.state_report(),
        user_settings=interface_manager,
        version=get_version(),
        can_manage_places=can_manage_places,
    )


# Also register as direct route (for backward compatibility)
def register_settings_page_route(app):
    """Register settings page route directly on app"""
    from flask import render_template
    from .settings_adapter import get_interface_manager
    
    try:
        from core.app_version import get_version
    except ImportError:
        def get_version():
            return "3.0.0"
    
    @app.route('/settings')
    def settings_page_direct():
        """Settings Page (direct route)"""
        interface_manager = get_interface_manager()
        from core.security import ROLE_ADMIN
        from core.security.flask_ext import current_user
        can_manage_places = getattr(current_user(), "role", None) == ROLE_ADMIN
        interfaces_by_category = interface_manager.get_interfaces_by_domain()
        all_interfaces = interface_manager.get_all_interfaces()
        interface_state = interface_manager.get_state()

        return render_template(
            'Settings/settings.html',
            interfaces_by_category=interfaces_by_category,
            all_interfaces=all_interfaces,
            interface_summary=interface_state.summary(),
            interface_state_report=interface_state.state_report(),
            user_settings=interface_manager,
            version=get_version(),
            can_manage_places=can_manage_places,
        )


# Register blueprint
def register_settings_routes(app):
    """Register settings blueprint and page route with Flask app"""
    # Register API blueprint
    app.register_blueprint(settings_bp)
    
    # Register page route directly
    register_settings_page_route(app)
    
    logger.info("✅ Settings API routes and page route registered")

