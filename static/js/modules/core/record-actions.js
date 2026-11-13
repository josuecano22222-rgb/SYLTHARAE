/**
 * The record action surface's runtime.
 *
 * The component draws the actions a record offers; this module runs the part a
 * server cannot render: asking the confirmation question, showing that a
 * control is busy, and ending the action in a toast. It performs no operation
 * of its own - no request, no navigation it decided on, no business rule.
 *
 * API
 *   RecordActions.bind(root, handler)        -> unbind()
 *   RecordActions.setBusy(root, actionId, on)
 *   RecordActions.setState(root, actionId, {enabled, reason, label})
 *   RecordActions.bound(root)                -> boolean
 *
 * `handler(actionId, context)` is the page's own function. It receives the
 * action id (the same id the Action Registry uses) and the element, returns
 * `{ok, message, detail, correlationId}` - or a promise of it - and does the
 * work: the fetch, the navigation, whatever the operation is. Returning
 * nothing means the page reports for itself; returning a result means the
 * surface reports it, so no action can end in silence.
 *
 * Two rules are enforced here rather than trusted to the page:
 *
 * * **A question that cannot be asked is not answered.** If a control declares
 *   a confirmation and the dialog component is not on the page, the action is
 *   refused rather than run unconfirmed. There is no `window.confirm` fallback
 *   in this module: one way of asking is the point of the component.
 * * **A busy control stays recognisable.** The label is kept and the control
 *   is marked `aria-busy`, disabled and (for buttons) shows the spinner that
 *   was already in the markup - the reader is told what is being waited for.
 */
(function (window, document) {
    'use strict';

    var BOUND = 'recordActionsBound';

    function text(node, value) {
        if (node && value !== undefined && value !== null) {
            node.textContent = value;
        }
    }

    function controls(root) {
        return Array.prototype.slice.call(
            root.querySelectorAll('[data-record-action]'));
    }

    function find(root, actionId) {
        var found = null;
        controls(root).forEach(function (node) {
            if (!found && node.getAttribute('data-record-action') === actionId) {
                found = node;
            }
        });
        return found;
    }

    function specFor(node) {
        var key = node.getAttribute('data-confirm-key');
        var message = node.getAttribute('data-confirm-message');
        if (!key && !message) {
            return null;
        }
        return {
            action: node.getAttribute('data-record-action'),
            title: node.getAttribute('data-confirm-title') || undefined,
            message: message || undefined,
            confirmLabel: node.getAttribute('data-confirm-accept-label') || undefined,
            cancelLabel: node.getAttribute('data-confirm-cancel-label') || undefined,
            dangerous: node.getAttribute('data-confirm-dangerous') === 'true',
            typed: node.getAttribute('data-confirm-typed') || undefined,
            confirmationKey: key || undefined,
        };
    }

    function setBusy(root, actionId, on) {
        var node = actionId ? find(root, actionId) : null;
        if (!node) {
            return;
        }
        var spinner = node.querySelector('[data-record-action-spinner]');
        if (spinner) {
            spinner.classList.toggle('d-none', !on);
        }
        if (node.tagName === 'BUTTON') {
            node.disabled = !!on;
        }
        node.setAttribute('aria-busy', on ? 'true' : 'false');
        root.setAttribute('aria-busy', on ? 'true' : 'false');
        if (on) {
            node.classList.add('disabled');
        } else if (!node.hasAttribute('data-disabled-reason')) {
            node.classList.remove('disabled');
        }
    }

    /**
     * Offer a control as enabled or disabled without re-rendering the record.
     *
     * The page that has just deleted the file the button points at says so
     * here. This is presentation only: the endpoint refuses the request on its
     * own, whether or not the button was grey.
     */
    function setState(root, actionId, state) {
        state = state || {};
        var node = find(root, actionId);
        if (!node) {
            return;
        }
        var enabled = state.enabled !== false;
        node.classList.toggle('disabled', !enabled);
        node.setAttribute('aria-disabled', enabled ? 'false' : 'true');
        if (node.tagName === 'BUTTON') {
            node.disabled = !enabled;
        }
        if (state.reason) {
            node.setAttribute('data-disabled-reason', state.reason);
        } else {
            node.removeAttribute('data-disabled-reason');
        }
        var described = node.getAttribute('aria-describedby');
        if (described) {
            var reason = document.getElementById(described);
            if (reason && state.label) {
                text(reason, state.label);
            }
        }
    }

    function report(node, result) {
        if (!result || typeof result !== 'object' || result.cancelled) {
            // A reader who closed the dialog did not ask for anything to be
            // reported - the operation simply was not performed.
            return;
        }
        var toast = window.Toast;
        if (!toast) {
            if (window.console) {
                console.error('RecordActions: no Toast runtime on this page');
            }
            return;
        }
        var options = {
            detail: result.detail || null,
            correlationId: result.correlationId || null,
        };
        // The words are the page's: it passed them in when it rendered the
        // action, and a handler may still say something more specific.
        var said = node.getAttribute(result.ok === false
            ? 'data-record-failure' : 'data-record-success');
        if (result.ok === false) {
            toast.error(result.message || said || '', options);
        } else if (result.message || said) {
            toast.success(result.message || said, options);
        }
    }

    function run(root, node, handler) {
        var actionId = node.getAttribute('data-record-action');
        var question = specFor(node);
        var href = node.getAttribute('href');

        function proceed() {
            var answered;
            try {
                answered = handler(actionId, {element: node, href: href});
            } catch (error) {
                answered = Promise.reject(error);
            }
            if (!answered || typeof answered.then !== 'function') {
                report(node, answered);
                return Promise.resolve(answered);
            }
            setBusy(root, actionId, true);
            return answered.then(function (result) {
                setBusy(root, actionId, false);
                report(node, result);
                if (result && result.ok === false) {
                    // The operation did not happen, so a link must not follow
                    // through to it either.
                    return result;
                }
                if (href && !(result && result.handled)) {
                    // Follow through the content swap when the shell
                    // provides it: an action that navigates must not
                    // redraw the sidebar either.
                    if (window.swapNavigate) { window.swapNavigate(href); }
                    else { window.location.href = href; }
                }
                return result;
            }, function () {
                setBusy(root, actionId, false);
                // A thrown handler is a defect, not a message: say that the
                // action did not happen and keep the technical detail out of
                // the reader's way.
                report(node, {ok: false});
                if (window.console) {
                    console.error('RecordActions: handler threw for ' + actionId);
                }
            });
        }

        if (!question) {
            return proceed();
        }
        if (!window.ConfirmDialog) {
            if (window.console) {
                console.error('RecordActions: ' + actionId
                    + ' needs a confirmation and this page has no dialog');
            }
            return Promise.resolve(false);
        }
        return window.ConfirmDialog.request(question).then(function (confirmed) {
            if (!confirmed) {
                return false;   // dismissing is never consent
            }
            return proceed();
        });
    }

    function bind(root, handler) {
        root = typeof root === 'string' ? document.getElementById(root) : root;
        if (!root) {
            if (window.console) {
                console.error('RecordActions: no surface to bind');
            }
            return function () {};
        }
        if (typeof handler !== 'function') {
            throw new Error('RecordActions.bind needs a handler function');
        }
        if (root.getAttribute(BOUND) === 'true') {
            // One binding per surface: two handlers on one button is how one
            // click performs twice.
            throw new Error('RecordActions: this surface is already bound');
        }
        root.setAttribute(BOUND, 'true');

        function onClick(event) {
            var node = event.target.closest('[data-record-action]');
            if (!node || !root.contains(node)) {
                return;
            }
            if (node.getAttribute('aria-disabled') === 'true'
                    || node.disabled) {
                event.preventDefault();
                return;
            }
            event.preventDefault();   // the runtime decides when it proceeds
            run(root, node, handler);
        }

        root.addEventListener('click', onClick);
        return function unbind() {
            root.removeEventListener('click', onClick);
            root.removeAttribute(BOUND);
        };
    }

    function bound(root) {
        root = typeof root === 'string' ? document.getElementById(root) : root;
        return !!(root && root.getAttribute(BOUND) === 'true');
    }

    window.RecordActions = {
        bind: bind,
        bound: bound,
        setBusy: setBusy,
        setState: setState,
    };
}(window, document));
