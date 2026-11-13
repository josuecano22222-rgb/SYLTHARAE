"""
Common routes (language, context processor, etc.)
"""

from flask import redirect, url_for, flash, session, request, g
from flask_babel import Babel, gettext as _
from datetime import datetime, date
import os
import time
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


def _password_min_length() -> int:
    """Return the valid password rule enforced by the auth service."""
    try:
        minimum = int(os.environ.get("PASSWORD_MIN_LENGTH", "12"))
    except (TypeError, ValueError):
        return 12
    return minimum if 8 <= minimum <= 128 else 12


def _interface_state():
    """The stored interface state, or None when it cannot be read.

    Used by the Jinja globals below. They are globals rather than context
    values because a Jinja *macro* cannot see the render context: a component
    imported with `{% from %}` gets its arguments and the environment globals,
    and nothing else. Passing the whole shell into every component call would
    put the wiring back into the pages.
    """
    try:
        from settings import get_interface_manager

        return get_interface_manager().get_state()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("interface state unavailable for template globals: %s", exc)
        return None


def feature_enabled(feature_id: str) -> bool:
    """Is a cross-cutting feature switched on? (Safe when state is unreadable.)"""
    state = _interface_state()
    return bool(state.is_enabled(feature_id)) if state is not None else False


def status_presentation(status):
    """How to present an application status (see core/frontend/status_vocabulary).

    A global rather than a context value because the badge component is a
    macro, and a macro cannot see the render context.
    """
    try:
        from core.frontend.status_vocabulary import present

        return present(status)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("status vocabulary unavailable for %r: %s", status, exc)

        class _Unknown(NamedTuple):
            status: str
            state: str = "neutral"
            label: str = ""
            known: bool = False

        return _Unknown(str(status or ""), label=str(status or ""))


def status_state(status):
    """Just the presentation state, for a caller that needs the word."""
    return status_presentation(status).state


def status_label(status):
    """Just the label, translated."""
    return _(status_presentation(status).label)


def status_vocabulary():
    """The vocabulary as a plain dict, for the JSON the page injects."""
    from core.frontend.status_vocabulary import VOCABULARY

    return {status: {"state": state, "label": label}
            for status, (state, label) in VOCABULARY.items()}


def interface_for(endpoint):
    """Read-only presentation lookup: what is this page called, and its icon?

    Answers identity questions only - never whether an interface is enabled,
    who may see it, or what it depends on. Those belong to the state service
    and arrive through `navigation` and `page_identity`.
    """
    try:
        from core.interfaces import get_interface_for_endpoint, status_policy

        interface = get_interface_for_endpoint(endpoint) if endpoint else None
        if interface is None:
            return None
        policy = status_policy(interface.status)
        return {
            "interface_id": interface.interface_id,
            "label": interface.name,
            "icon": interface.icon,
            "description": interface.description,
            "domain": str(interface.domain),
            "help_topic": interface.help_topic,
            "shortcut": interface.keyboard_shortcut,
            "status": str(interface.status),
            "badge": policy.badge,
            "note": policy.note,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("interface_for(%r) failed: %s", endpoint, exc)
        return None


def resolve_interface_language(app):
    """Return the interface language for the current request.

    Precedence:
      1. ``user_language`` cookie. This is the most recent explicit choice made
         in this browser, by the setup wizard, the login screen, or the header
         switcher. It applies with or without a session.
      2. ``session['language']``, set by ``/set_language`` for signed-in users.
      3. ``system.language`` from settings (the installation's default).
      4. The browser's Accept-Language preference.
      5. ``BABEL_DEFAULT_LOCALE``.

    This function never writes to the session. Storing the system default in the
    session on a first visit used to pin that language, so a later explicit choice
    was ignored.
    """
    from flask import request, session

    supported = app.config['LANGUAGES']
    cookie_lang = request.cookies.get('user_language')
    if cookie_lang in supported:
        return cookie_lang

    session_lang = session.get('language')
    if session_lang in supported:
        return session_lang

    if app.config.get('SETTINGS'):
        try:
            system_language = app.config['SETTINGS'].get_system_config().get('language', 'en')
            if system_language in supported:
                return system_language
        except Exception:  # settings are optional for this lookup
            pass

    browser_lang = request.accept_languages.best_match(list(supported.keys()))
    if browser_lang:
        return browser_lang
    return app.config['BABEL_DEFAULT_LOCALE']


#: Endpoints that answer with a machine-readable body and never render a
#: template. They must not wait on the database before answering, because
#: reporting an outage is what they are for.
_MACHINE_ONLY_ENDPOINTS = ("health.",)


def _answers_without_templates(endpoint) -> bool:
    """Does ``endpoint`` answer without rendering anything to translate?"""
    return bool(endpoint) and str(endpoint).startswith(_MACHINE_ONLY_ENDPOINTS)


def register_common_routes(app, babel_instance):
    """Register common routes and context processors with the Flask app"""
    
    def get_locale():
        """Interface language for the current request (see resolve_interface_language)."""
        return resolve_interface_language(app)

    @app.route('/set_language/<language>')
    def set_language(language):
        """Route to switch language - SYSTEM-WIDE"""
        if language in app.config['LANGUAGES']:
            session['language'] = language
            try:
                from settings import get_settings
                settings = get_settings()
                # Use dot notation for unified settings
                settings.set_setting('system.language', language)
                # Update app config
                app.config['BABEL_DEFAULT_LOCALE'] = language
                if 'SETTINGS' in app.config and app.config['SETTINGS']:
                    app.config['SETTINGS'].system.language = language
                    # Force reload to clear cache
                    app.config['SETTINGS'].reload_from_file()
                logger.info(f"✅ Language updated system-wide via /set_language: {language} (session updated first)")
            except Exception as e:
                logger.warning(f"Could not update system settings for language: {e}")
            
            # Force locale immediately for this request
            if babel_instance:
                from flask_babel import force_locale
                with force_locale(language):
                    flash(_('Language changed to %(lang)s', lang=app.config["LANGUAGES"][language]), 'info')
            else:
                flash(_('Language changed to %(lang)s', lang=app.config["LANGUAGES"][language]), 'info')
        
        # Support AJAX requests
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            from flask import jsonify
            response = jsonify({
                'success': True,
                'language': language,
                'language_name': app.config['LANGUAGES'].get(language, language),
                'message': _('Language changed to %(lang)s', lang=app.config["LANGUAGES"].get(language, language))
            })
        else:
            response = redirect(request.referrer or url_for('index'))
        if language in app.config['LANGUAGES']:
            # The cookie is the explicit choice for this browser; it must win over any older session value.
            response.set_cookie('user_language', language, max_age=31536000, samesite='Lax', path='/')
        return response
    
    # Components (macros) cannot read the render context, so the two lookups
    # they need are environment globals. A page still receives the prepared
    # shell through the context processor below.
    app.jinja_env.globals.setdefault("feature_enabled", feature_enabled)
    app.jinja_env.globals.setdefault("interface_for", interface_for)
    app.jinja_env.globals.setdefault("status_presentation", status_presentation)
    app.jinja_env.globals.setdefault("status_state", status_state)
    app.jinja_env.globals.setdefault("status_label", status_label)

    @app.context_processor
    def inject_now():
        """Inject datetime functions and translation helper into templates"""
        from flask_wtf.csrf import generate_csrf
        
        # Import centralized version
        try:
            from core.app_version import get_version
            app_version = get_version()
        except ImportError:
            app_version = "unknown"  # version.py is the single source of truth
        
        context = {
            'now': datetime.now,
            'datetime': datetime,
            'date': date,
            '_': _,  # Translation function
            'get_locale': get_locale,
            'languages': app.config.get('LANGUAGES', {}),
            'current_language': get_locale(),
            'csrf_token': generate_csrf,  # CSRF token function for templates
            'version': app_version,  # Application version
            # Keep password guidance in the UI consistent with the rule the
            # auth service enforces (PASSWORD_MIN_LENGTH is the source used by
            # core.security.service). Clamp malformed configuration to the
            # secure default instead of breaking page rendering.
            'password_min_length': _password_min_length(),
            # The Screen Inspector, off unless both the installation and this
            # account say otherwise. Filled in below with the settings and the
            # registry; the endpoint it reads is guarded on its own, because a
            # tool nobody renders is not a tool nobody can call.
            'inspector_enabled': False,
            'inspector_interface': None,
        }
        
        # Safely inject interface manager - wrap in try-except to prevent cascading errors
        try:
            from settings import get_interface_manager
            interface_manager = get_interface_manager()
            context['interface_manager'] = interface_manager
            context['is_interface_enabled'] = interface_manager.is_interface_enabled
            context['is_interface_enabled_by_endpoint'] = interface_manager.is_interface_enabled_by_endpoint
            # Also inject as user_settings for template compatibility
            context['user_settings'] = interface_manager

            # The shell is prepared here, not in markup.
            #
            # `navigation` is a list of domains with ready-to-render entries
            # (label, url, icon, active state, badge, shortcut) and `page` is
            # the current page's identity (label, domain, icon, help topic,
            # breadcrumbs). The template renders them and decides nothing: it
            # must not ask whether an interface is enabled, who owns it, or
            # what role it needs.
            from core.interfaces import (
                get_interface, get_interfaces_by_domain, present_page,
                build_navigation, get_features,
            )
            interface_state = interface_manager.get_state()
            context['interface_state'] = interface_state
            context['interface_registry'] = get_interface
            context['interfaces_by_domain'] = get_interfaces_by_domain()

            # Cross-cutting features (page tips and the like) are state, so a
            # component asks for its own feature and no template names a
            # product id to gate markup.
            context['features'] = {
                feature.feature_id: interface_state.is_enabled(feature.feature_id)
                for feature in get_features()
            }

            current_user_obj = getattr(g, 'user', None)
            # The user's own sidebar preferences (hide / position). Kept
            # fail-open to the declared navigation: if the preference store
            # cannot be read, the registry's order and visibility stand -
            # the shell never blanks the navigation over a preference error.
            nav_prefs = None
            if current_user_obj is not None and getattr(current_user_obj, 'id', None):
                try:
                    from services.navigation_prefs import get_prefs as _get_nav_prefs
                    from Api.utils.utils import get_connection as _nav_conn
                    with _nav_conn() as _nav_conn_handle:
                        nav_prefs = _get_nav_prefs(_nav_conn_handle,
                                                   current_user_obj.id)
                except Exception as nav_pref_error:
                    logger.debug("navigation preferences unavailable: %s",
                                 nav_pref_error)
                    nav_prefs = None
            context['navigation'] = build_navigation(
                interface_state, current_user_obj, request.endpoint, url_for,
                prefs=nav_prefs)
            # Named `page_identity`, not `page`: several routes already pass
            # `page` as the current pagination cursor, and a template that
            # received a number where it expected the page model (or the other
            # way round) would fail quietly.
            context['page_identity'] = present_page(
                request.endpoint, interface_state, current_user_obj, url_for)
            # The status vocabulary, for the JavaScript that renders a status
            # chip when a page updates without reloading. Injected as data, so
            # the mapping has one owner (core/frontend/status_vocabulary.py)
            # and both renderers read it.
            context['status_vocabulary'] = status_vocabulary()

            # The Screen Inspector is offered only when the installation
            # switched it on and the account is an administrator. The interface
            # it will inspect is the registry's answer for this request's
            # endpoint - never parsed from the URL - so the panel can say which
            # screen it is showing even before anything is selected.
            try:
                from core.interfaces import get_interface_for_endpoint
                context['inspector_interface'] = getattr(
                    get_interface_for_endpoint(request.endpoint), 'interface_id', None)
                context['inspector_enabled'] = bool(
                    current_user_obj and getattr(current_user_obj, 'is_admin', False)
                    and interface_manager.get('system', 'screen_inspector', False))
            except Exception as inspector_error:  # pragma: no cover - defensive
                logger.warning(f"Screen Inspector not enabled: {inspector_error}")
                context['inspector_enabled'] = False
                context['inspector_interface'] = None

            # `interface_for` is an environment global (see above): components
            # need it, and macros cannot see this context.
        except Exception as e:
            logger.warning(f"Failed to load interface manager in context processor: {e}")
            # The registry is what decides whether an interface exists, so a
            # failure here must not become "everything is enabled": templates
            # get the strict answers (nothing is enabled, nothing is visible)
            # and the page renders its empty state rather than claiming a
            # product surface that could not be verified.
            context['interface_manager'] = None
            context['user_settings'] = None
            context['interface_state'] = None
            context['interface_registry'] = lambda interface_id: None
            context['interfaces_by_domain'] = {}
            context['navigation'] = ()
            context['features'] = {}
            context['page_identity'] = None
            context['is_interface_enabled'] = lambda interface_id: False
            context['is_interface_enabled_by_endpoint'] = lambda endpoint: False
        
        return context
    
    @app.before_request
    def before_request():
        """Track request start time for profiling, ensure locale is set, and check interface access"""
        g.start_time = time.time()
        
        # Check interface access - block disabled interfaces
        # Skip check for static files, API endpoints, setup, and settings page (always accessible)
        # Settings page must always be accessible so users can re-enable interfaces
        #
        # OPS-05b: 'index' (the dashboard / home page) is exempt. The gate
        # redirects a blocked endpoint to url_for('index'); if the index is
        # itself subject to the gate and reads as disabled, that redirects to
        # itself and the browser reports ERR_TOO_MANY_REDIRECTS, leaving the
        # application unusable with no way back into Settings to re-enable
        # anything. The home page must therefore never be gated.
        from core.interfaces import SYSTEM_ENDPOINTS, is_infrastructure_endpoint

        endpoint = request.endpoint
        settings_endpoint = bool(endpoint) and endpoint.startswith(('settings_page', 'settings_api.'))
        infrastructure = is_infrastructure_endpoint(endpoint, request.path)

        if (endpoint and endpoint != 'static' and endpoint != 'index'
                and not infrastructure
                and endpoint not in SYSTEM_ENDPOINTS
                and not settings_endpoint
                and not request.path.startswith('/static')
                and not request.path.startswith('/settings')):

            try:
                from settings import get_interface_manager
                interface_manager = get_interface_manager()

                # The interface that owns this endpoint decides whether it is
                # served. An endpoint no interface owns is refused: the old
                # behaviour ("not in the map, therefore enabled") made an
                # unregistered page indistinguishable from a registered one,
                # which is exactly the gap the registry exists to close.
                if not interface_manager.is_interface_enabled_by_endpoint(endpoint):
                    logger.info("Access denied to disabled or unregistered endpoint: %s", endpoint)
                    flash(_('This section is currently disabled. Please enable it in Settings to access.'), 'warning')
                    return redirect(url_for('index'))
            except Exception as check_error:
                # A check that could not run is not permission to proceed.
                #
                # This used to log at debug level and serve the page anyway,
                # which meant a broken interface state was indistinguishable
                # from a working one: the operator saw a normal page and no
                # indication that the product's own gate had failed. The error
                # now goes through the common pipeline (correlation id, details
                # server-side only, generic message to the browser), and the
                # dashboard and Settings stay exempt so the failure can be
                # inspected and repaired.
                logger.warning(
                    "Interface access check failed for endpoint %s: %s",
                    endpoint, check_error,
                )
                raise

        # An explicit client choice (X-Language with X-Language-Explicit) is the
        # newest one. It applies to this request, is stored in the session, and
        # the cookie is refreshed in after_request. Every other request uses the
        # shared resolver. Non-explicit hints are not stored anywhere.
        request_lang = request.headers.get('X-Language')
        is_explicit = request.headers.get('X-Language-Explicit') == 'true'
        if is_explicit and request_lang in app.config['LANGUAGES']:
            locale = request_lang
            if session.get('language') != locale:
                session['language'] = locale
                session.permanent = True
            g._language_cookie = locale
        else:
            locale = resolve_interface_language(app)
        
        if locale and locale in app.config['LANGUAGES']:
            # Store locale in g for use in templates and Babel
            g.locale = locale
            
            # Ensure Babel uses the correct locale for this request
            if babel_instance:
                from flask_babel import force_locale
                # Force locale for this request context
                g._babel_locale = locale

            # Runtime edits live in the database, but gettext remains the
            # source of truth for every screen. Overlay only this request's
            # Babel catalog so server-rendered labels and the public JS catalog
            # observe the same saved translation.
            # The liveness probe is exempt: it returns JSON with no user-facing
            # string to translate, and the overlay reads the database - so with
            # the database down the probe waited out the 30 s pool timeout
            # before the route that reports the outage could run. Measured on
            # the running application: 30.0 s per /health with PostgreSQL
            # stopped (tests/unit/test_health_probe_not_blocked.py).
            if request.endpoint != 'static' and not _answers_without_templates(request.endpoint):
                try:
                    from Api.services.translation_management import apply_runtime_translation_overrides
                    apply_runtime_translation_overrides(locale)
                except Exception as override_error:
                    # Translation persistence must never make an otherwise
                    # healthy page unavailable (for example during first-run
                    # setup before migrations have completed).
                    logger.warning(
                        "Translation overrides unavailable for %s (%s)",
                        locale, override_error.__class__.__name__,
                    )
        
        # Check for settings file changes periodically (every 10th request to avoid overhead)
        # This allows external modifications to settings.json to be picked up
        if not hasattr(g, '_settings_check_count'):
            g._settings_check_count = 0
        g._settings_check_count += 1
        
        if g._settings_check_count % 10 == 0:  # Check every 10 requests
            try:
                from settings import reload_settings_from_file, get_settings
                if reload_settings_from_file():
                    settings = get_settings()
                    # Update app config if language/timezone changed
                    system_config = settings.get_system_config()
                    if 'BABEL_DEFAULT_LOCALE' in app.config:
                        app.config['BABEL_DEFAULT_LOCALE'] = system_config.get('language', 'en')
                    if 'BABEL_DEFAULT_TIMEZONE' in app.config:
                        app.config['BABEL_DEFAULT_TIMEZONE'] = system_config.get('timezone', 'UTC')
                    logger.info("✅ Settings reloaded from file during request")
            except Exception as e:
                # Don't fail requests if settings reload fails
                logger.debug(f"Settings reload check failed: {e}")
    
    @app.after_request
    def after_request(response):
        """Set language header in response and log slow endpoints"""
        # Set response header to communicate language back to client
        if hasattr(g, 'locale') and g.locale:
            response.headers['X-Language'] = g.locale
        # A request that made an explicit language choice hands its value to us
        # here (see before_request and the Settings routes). The cookie is what
        # makes the choice survive, so it is refreshed on the same response.
        pending_language = getattr(g, '_language_cookie', None)
        if pending_language in app.config['LANGUAGES']:
            response.set_cookie('user_language', pending_language, max_age=31536000, samesite='Lax', path='/')
        
        # Log slow endpoints
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            if elapsed > 2.0:  # Log requests taking > 2 seconds
                logger.warning(f"⚠️ SLOW ENDPOINT ({elapsed:.2f}s): {request.method} {request.path}")
        
        return response
    
    
    @app.template_filter('format_date')
    def format_date_filter(value, format_string='%Y-%m-%d'):
        """
        Format a date/datetime value, handling both datetime objects and strings.
        
        Args:
            value: datetime, date, or string value
            format_string: strftime format string (default: '%Y-%m-%d')
        
        Returns:
            Formatted date string or 'N/A' if value is None or invalid
        """
        if value is None:
            return 'N/A'
        
        try:
            # If it's already a datetime or date object, use strftime
            if hasattr(value, 'strftime'):
                return value.strftime(format_string)
            
            # If it's a string, try to parse it
            if isinstance(value, str):
                # Try common date formats
                from datetime import datetime
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
                    try:
                        dt = datetime.strptime(value, fmt)
                        return dt.strftime(format_string)
                    except ValueError:
                        continue
                # If parsing fails, return the string as-is (might already be formatted)
                return value
            
            # For any other type, try to convert to string
            return str(value)
        except Exception as e:
            logger.warning(f"Error formatting date {value}: {e}")
            return 'N/A'
    
    @app.template_filter('number_format')
    def number_format(value):
        """
        Format number with thousand separators.
        
        Args:
            value: Number to format
            
        Returns:
            Formatted string with commas or original value if invalid
        """
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

