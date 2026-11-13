/**
 * The toast runtime: the one place the application says what happened.
 *
 * Rules this module enforces, because they are the difference between a
 * notification and a leak:
 *
 *   - every message is written with `textContent`, so a message can never
 *     become markup, whatever the caller passes;
 *   - a message is never built from an error object here. Callers pass a
 *     sentence and, at most, a correlation id - the shared error pipeline has
 *     already turned the exception into something safe to show;
 *   - a failure is persistent and dismissible; a success or a note fades on its
 *     own. Nothing disappears before it has been read unless the reader says so.
 *
 * API
 *   Toast.show({tone, title, message, detail, actionLabel, actionHref, onAction,
 *               correlationId, persistent, dismissible, timeout})
 *   Toast.success(message, options) / .info / .warning / .error
 *   Toast.clear(region)
 *
 * Tones: success, info, warning, error.
 */
(function (window, document) {
    'use strict';

    //: Bootstrap's own palette, chosen once here so two pages cannot disagree.
    var TONES = {
        success: {classes: 'text-bg-success', icon: 'bi-check-circle'},
        info: {classes: 'text-bg-primary', icon: 'bi-info-circle'},
        warning: {classes: 'text-bg-warning', icon: 'bi-exclamation-triangle'},
        error: {classes: 'text-bg-danger', icon: 'bi-x-octagon'},
    };

    //: A success or a note is read in passing; a warning or a failure stays.
    var PERSISTENT = {success: false, info: false, warning: true, error: true};
    var DEFAULT_TIMEOUT = 5000;

    function region(root) {
        if (!root) {
            return document.querySelector('[data-toast-region]');
        }
        return typeof root === 'string' ? document.getElementById(root) : root;
    }

    function build(spec) {
        var tone = TONES[spec.tone] ? spec.tone : 'info';
        var surface = TONES[tone];
        var persistent = spec.persistent === undefined
            ? PERSISTENT[tone] : !!spec.persistent;
        var dismissible = spec.dismissible === undefined ? true : !!spec.dismissible;

        var node = document.createElement('div');
        node.className = 'toast ' + surface.classes;
        node.setAttribute('role', tone === 'error' ? 'alert' : 'status');
        node.setAttribute('aria-live', tone === 'error' ? 'assertive' : 'polite');
        node.setAttribute('aria-atomic', 'true');
        node.setAttribute('data-toast', tone);
        if (spec.correlationId) {
            node.setAttribute('data-correlation-id', String(spec.correlationId));
        }

        var body = document.createElement('div');
        body.className = 'd-flex';

        var icon = document.createElement('i');
        icon.className = 'bi ' + surface.icon + ' me-2';
        icon.setAttribute('aria-hidden', 'true');

        var text = document.createElement('div');
        text.className = 'toast-body flex-grow-1';

        if (spec.title) {
            var title = document.createElement('div');
            title.className = 'fw-semibold';
            title.setAttribute('data-toast-title', '');
            title.textContent = String(spec.title);
            text.appendChild(title);
        }

        // Text, never markup: a message cannot become HTML here.
        var message = document.createElement('div');
        message.setAttribute('data-toast-message', '');
        message.textContent = String(spec.message === undefined ? '' : spec.message);
        text.appendChild(message);

        if (spec.detail) {
            var detail = document.createElement('div');
            detail.className = 'small opacity-75';
            detail.setAttribute('data-toast-detail', '');
            detail.textContent = String(spec.detail);
            text.appendChild(detail);
        }

        if (spec.correlationId) {
            // The one technical string that helps an operator and reveals
            // nothing: the id that ties this to the server's own log line.
            var correlation = document.createElement('div');
            correlation.className = 'small font-monospace';
            correlation.setAttribute('data-toast-correlation', '');
            correlation.textContent = 'ID: ' + String(spec.correlationId);
            text.appendChild(correlation);
        }

        if (spec.actionLabel && spec.actionHref) {
            var action = document.createElement('a');
            action.className = 'toast-action small d-inline-block mt-1';
            action.setAttribute('data-toast-action', '');
            action.href = String(spec.actionHref);
            action.textContent = String(spec.actionLabel);
            text.appendChild(action);
        } else if (spec.actionLabel && typeof spec.onAction === 'function') {
            // A callback action (for example "Undo"): a real button, so it is
            // keyboard-reachable and never navigates.
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'toast-action btn btn-link btn-sm p-0 mt-1 text-white';
            button.setAttribute('data-toast-action', '');
            button.textContent = String(spec.actionLabel);
            button.addEventListener('click', function () {
                spec.onAction();
            });
            text.appendChild(button);
        }

        body.appendChild(icon);
        body.appendChild(text);

        if (dismissible) {
            var close = document.createElement('button');
            close.type = 'button';
            close.className = 'btn-close btn-close-white me-2 m-auto';
            close.setAttribute('data-bs-dismiss', 'toast');
            close.setAttribute('aria-label', spec.dismissLabel || 'Close');
            body.appendChild(close);
        }

        node.appendChild(body);
        return {node: node, persistent: persistent,
                timeout: spec.timeout === undefined ? DEFAULT_TIMEOUT : spec.timeout};
    }

    function show(spec) {
        spec = spec || {};
        var host = region(spec.root);
        if (!host) {
            // Never silently: a message nobody sees is the failure this
            // component exists to prevent. Say so in the console instead.
            if (window.console) {
                console.warn('Toast: no [data-toast-region] on this page');
            }
            return null;
        }
        var built = build(spec);
        host.appendChild(built.node);

        var instance = window.bootstrap && window.bootstrap.Toast
            ? window.bootstrap.Toast.getOrCreateInstance(built.node, {
                autohide: !built.persistent,
                delay: built.timeout,
            })
            : null;

        built.node.addEventListener('hidden.bs.toast', function () {
            if (built.node.parentNode) {
                built.node.parentNode.removeChild(built.node);
            }
        });
        if (instance) {
            instance.show();
        } else {
            // No Bootstrap: the message still appears, and still goes away if
            // it was not meant to stay.
            if (!built.persistent && built.timeout) {
                window.setTimeout(function () {
                    if (built.node.parentNode) {
                        built.node.parentNode.removeChild(built.node);
                    }
                }, built.timeout);
            }
        }
        return built.node;
    }

    function clear(root) {
        var host = region(root);
        if (host) {
            host.textContent = '';
        }
    }

    function shortcut(tone) {
        return function (message, options) {
            return show(Object.assign({}, options || {}, {tone: tone, message: message}));
        };
    }

    window.Toast = {
        show: show,
        clear: clear,
        success: shortcut('success'),
        info: shortcut('info'),
        warning: shortcut('warning'),
        error: shortcut('error'),
    };
}(window, document));
