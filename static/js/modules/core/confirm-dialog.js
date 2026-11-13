/**
 * The confirmation dialog's runtime.
 *
 * The component renders the question; this module asks it and reports the
 * answer. It performs nothing: no request, no navigation, no business rule.
 * The page decides what happens when the answer is yes, exactly as it did
 * before - the difference is that the question is now one component instead of
 * a browser `confirm()` in every page that needed one.
 *
 * One rule is enforced here rather than trusted to callers: a destructive
 * action asks about a *named* action
 * (`data-confirm-action="files.delete_selected"`). If a page forgets to say
 * which action it is confirming, the dialog still works, and the Screen
 * Inspector has something honest to report about it.
 *
 * API
 *   ConfirmDialog.request(spec) -> Promise<boolean>
 *   ConfirmDialog.setLoading(root, on)
 *   ConfirmDialog.setError(root, message)
 *
 * `spec` is presentation and context only:
 *   action         action id from the Action Registry, e.g. 'jobs.cancel'
 *   title          headline (a sentence, already translated by the page)
 *   message        what will happen
 *   scope          scope text, e.g. '27 files' - the same idea as the toolbar's
 *   confirmLabel   the verb on the accepting button
 *   cancelLabel    the verb on the cancelling button
 *   dangerous      draws it in the danger tone
 *   typed          word the operator must type before the button unlocks
 *   root           the dialog element, or its id; defaults to [data-confirm-dialog]
 */
(function (window, document) {
    'use strict';

    function find(root) {
        if (!root) {
            return document.querySelector('[data-confirm-dialog]');
        }
        return typeof root === 'string' ? document.getElementById(root) : root;
    }

    function text(node, value) {
        if (node && value !== undefined && value !== null) {
            node.textContent = value;
        }
    }

    function show(node, value) {
        if (!node || !value) {
            return;
        }
        node.classList.remove('d-none');
        text(node.querySelector('[data-confirm-scope]'), value);
        var line = node.querySelector('[data-confirm-scope-line]');
        if (line) {
            line.classList.remove('d-none');
        }
    }

    function applySpec(node, spec) {
        spec = spec || {};
        var title = node.querySelector('.modal-title');
        if (title && spec.title) {
            title.textContent = spec.title;
            var icon = document.createElement('i');
            icon.className = 'bi ' + (spec.dangerous
                ? 'bi-exclamation-triangle' : 'bi-question-circle') + ' me-2';
            icon.setAttribute('aria-hidden', 'true');
            title.insertBefore(icon, title.firstChild);
        }
        text(node.querySelector('[data-confirm-message]'), spec.message);
        if (spec.scope) {
            show(node, spec.scope);
        }
        text(node.querySelector('[data-confirm-accept-label]'), spec.confirmLabel);
        var cancel = node.querySelector('[data-confirm-cancel]');
        text(cancel, spec.cancelLabel);
        var close = node.querySelector('.btn-close');
        if (close && spec.cancelLabel) {
            close.setAttribute('aria-label', spec.cancelLabel);
        }
        if (spec.action) {
            node.setAttribute('data-confirm-action', spec.action);
        }
        if (spec.confirmationKey) {
            node.setAttribute('data-confirm-key', spec.confirmationKey);
        }
        node.setAttribute('data-confirm-dangerous', spec.dangerous ? 'true' : 'false');
        var accept = node.querySelector('[data-confirm-accept]');
        if (accept && spec.dangerous !== undefined) {
            accept.classList.toggle('btn-danger', !!spec.dangerous);
            accept.classList.toggle('btn-primary', !spec.dangerous);
        }
        setError(node, '');
        setLoading(node, false);
        armTyping(node, spec.typed);
    }

    /** The typed-confirmation rule: the button unlocks on an exact match. */
    function armTyping(node, word) {
        var field = node.querySelector('[data-confirm-typed-input]');
        var accept = node.querySelector('[data-confirm-accept]');
        if (!field || !accept) {
            return;
        }
        if (!word) {
            field.value = '';
            accept.disabled = false;
            accept.setAttribute('aria-disabled', 'false');
            return;
        }
        var check = function () {
            var ok = field.value.trim() === word;
            accept.disabled = !ok;
            accept.setAttribute('aria-disabled', ok ? 'false' : 'true');
        };
        field.oninput = check;
        field.value = '';
        check();
    }

    function setLoading(root, on) {
        var node = find(root);
        if (!node) {
            return;
        }
        var accept = node.querySelector('[data-confirm-accept]');
        var cancel = node.querySelector('[data-confirm-cancel]');
        var spinner = node.querySelector('[data-confirm-spinner]');
        if (spinner) {
            spinner.classList.toggle('d-none', !on);
        }
        if (accept) {
            var typed = node.getAttribute('data-confirm-typed');
            var field = node.querySelector('[data-confirm-typed-input]');
            var satisfied = !typed || (field && field.value.trim() === typed);
            accept.disabled = !!on || !satisfied;
            accept.setAttribute('aria-disabled', accept.disabled ? 'true' : 'false');
        }
        if (cancel) {
            cancel.disabled = !!on;
        }
        node.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    function setError(root, message) {
        var node = find(root);
        var box = node && node.querySelector('[data-confirm-error]');
        if (!box) {
            return;
        }
        box.textContent = message || '';
        box.classList.toggle('d-none', !message);
    }

    /**
     * Open the dialog and resolve with the operator's answer.
     *
     * Resolves `false` for Escape, the close button, the cancel button and a
     * click outside - dismissing a destructive question is never consent.
     */
    function request(spec, root) {
        spec = spec || {};
        var node = find(root || spec.root);
        if (!node) {
            // No dialog on this page: refuse rather than invent one. A page
            // that asks for confirmation must render the component, so that
            // the question is one component everywhere instead of a string.
            if (window.console) {
                console.error('ConfirmDialog: no [data-confirm-dialog] on this page');
            }
            return Promise.resolve(false);
        }
        applySpec(node, spec);

        return new Promise(function (resolve) {
            var instance = window.bootstrap && window.bootstrap.Modal
                ? window.bootstrap.Modal.getOrCreateInstance(node)
                : null;

            function settle(value) {
                node.removeEventListener('click', onClick);
                node.removeEventListener('hidden.bs.modal', onHidden);
                // Move focus out of the dialog before it is hidden (aria-hidden
                // must not be set on an ancestor that still holds focus).
                if (document.activeElement && node.contains(document.activeElement)) {
                    document.activeElement.blur();
                }
                if (instance) {
                    instance.hide();
                }
                resolve(value);
            }

            function onClick(event) {
                if (event.target.closest('[data-confirm-accept]')) {
                    settle(true);
                } else if (event.target.closest('[data-confirm-cancel]')) {
                    settle(false);
                }
            }

            function onHidden() {
                // Escape, the close button, a backdrop click: all "no".
                node.removeEventListener('click', onClick);
                node.removeEventListener('hidden.bs.modal', onHidden);
                resolve(false);
            }

            node.addEventListener('click', onClick);
            node.addEventListener('hidden.bs.modal', onHidden);
            if (instance) {
                instance.show();
            } else {
                // Without Bootstrap the question is still asked, natively.
                resolve(window.confirm(spec.message || spec.title || ''));
            }
        });
    }

    window.ConfirmDialog = {
        request: request,
        setLoading: setLoading,
        setError: setError,
    };
}(window, document));
