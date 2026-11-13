/**
 * The Screen Inspector's runtime.
 *
 * The panel says what an element on this screen *is*: which interface owns it,
 * which component renders it, which action it presents, what that action
 * declares, where it is bound, and what it currently is. The server answers -
 * from the interface registry, the action registry, the binding scan and the
 * component library - and this module does the part a server cannot:
 *
 *   * activation, once per page, however many times a page asks (`init()` is
 *     idempotent: a second call re-uses the listeners rather than adding
 *     another set, because two sets of listeners is one click inspected twice);
 *   * targeting: highlight what is under the pointer, and select it on click
 *     without letting the click reach the control - inspecting a Delete button
 *     must never delete anything;
 *   * Escape: leave targeting first, then leave the mode;
 *   * focus: the panel takes focus while it is open, stays reachable by
 *     keyboard, and gives focus back to what it was opened from;
 *   * the fetch, and the panel's own states - loading, empty, error - so no
 *     inspection ever shows the previous element's answers.
 *
 * API
 *   ScreenInspector.init(options)     -> idempotent; returns the instance
 *   ScreenInspector.enable() / .disable() / .isEnabled()
 *   ScreenInspector.open(element) / .close()
 *   ScreenInspector.inspect(element)  -> Promise of the answer, or null
 *   ScreenInspector.active()          -> the element the panel describes
 *
 * It never calls a business endpoint, never executes an action and never
 * decides anything: the one request it makes is a GET to the read-only
 * inspector endpoint, and the answer is drawn as text.
 */
(function (window, document) {
    'use strict';

    var OPTIONS = {
        root: '[data-screen-inspector]',
        panel: '[data-inspector-panel]',
        toggle: '[data-inspector-toggle]',
        announce: '[data-inspector-announce]',
        endpoint: '/api/experience/inspect',
        targetClass: 'inspector-target',
        // Elements that are never worth inspecting as a target: the panel
        // itself, its toggle, and the document scaffolding.
        ignore: '[data-screen-inspector], [data-inspector-panel], script, style, link, meta',
    };

    var STATE = {
        initialised: false,
        mode: false,
        active: null,
        //: The element that had focus when the panel opened, so it can have it
        //: back: an inspector that loses your place costs more than it gives.
        returnFocusTo: null,
        loading: false,
        request: 0,
        instance: null,
    };

    var ANNOUNCED = {
        mode_on: 'Inspector on: choose an element to inspect.',
        mode_off: 'Inspector off.',
        loading: 'Loading inspector information.',
        empty: 'Select an element to inspect its SYLTHARAE contract.',
        failed: 'Inspector information could not be loaded.',
    };

    function text(node, value) {
        if (node && value !== undefined && value !== null) {
            node.textContent = value;
        }
    }

    function root() {
        return document.querySelector(OPTIONS.root);
    }

    function panel() {
        return document.querySelector(OPTIONS.panel);
    }

    function announce(node, message) {
        var region = document.querySelector(OPTIONS.announce);
        if (region) {
            // One polite region for the whole tool: the reader is told what
            // happened without the panel stealing the whole page.
            text(region, message);
        }
        if (node && window.console && window.console.debug) {
            window.console.debug('ScreenInspector: ' + message);
        }
    }

    function candidate(node) {
        if (!node || node.nodeType !== 1) {
            return null;
        }
        // The inspector's own surface is never a target: the toggle is a
        // control of this tool, and inspecting it would swallow the click that
        // switches the mode off. Anything inside the host is out of bounds.
        if (node.closest && node.closest(OPTIONS.ignore)) {
            return null;
        }
        return node;
    }

    /** Everything the server needs to identify this element, and nothing else. */
    function describe(node) {
        var attributes = {
            interface: 'data-inspector-interface',
            component: 'data-inspector-component',
            action: 'data-inspector-action',
            binding_kind: 'data-inspector-binding',
            binding_source: 'data-inspector-binding-source',
            role: 'data-inspector-role',
        };
        var query = [];
        var known = {};
        Object.keys(attributes).forEach(function (name) {
            var value = node.getAttribute(attributes[name]);
            if (value) {
                known[attributes[name]] = true;
                query.push(name + '=' + encodeURIComponent(value));
            }
        });

        // An element inside a region that described itself, such as a row in a
        // record list, is described by the nearest ancestor that did.
        var owner = node.closest(Object.keys(attributes).map(function (name) {
            return '[' + attributes[name] + ']';
        }).join(','));
        if (owner && owner !== node) {
            Object.keys(attributes).forEach(function (name) {
                if (known[attributes[name]]) {
                    return;
                }
                var value = owner.getAttribute(attributes[name]);
                if (value) {
                    query.push(name + '=' + encodeURIComponent(value));
                }
            });
        }

        var classes = (node.className || '').split(/\s+/).filter(Boolean).slice(0, 40);
        if (classes.length) {
            query.push('classes=' + encodeURIComponent(classes.join(',')));
        }
        var page = document.body && document.body.getAttribute('data-current-endpoint');
        if (page) {
            // The screen comes from the registry; this is only the endpoint the
            // page was served by, and the server looks the interface up itself.
            query.push('endpoint=' + encodeURIComponent(page));
        }
        var observed = (node.getAttribute('data-inspector-text')
                        || node.textContent || '').trim().slice(0, 200);
        if (observed) {
            query.push('text=' + encodeURIComponent(observed));
        }
        // How many rows are selected is a presentation fact the toolbar already
        // keeps in the markup; the Inspector reads it rather than asking the
        // runtime for a second API, so the frozen toolbar does not grow one.
        var bar = node.closest('[data-action-toolbar]');
        if (bar) {
            var selected = bar.getAttribute('data-selected-count');
            if (selected !== null && selected !== '') {
                query.push('selected=' + encodeURIComponent(selected));
                var summary = bar.querySelector('[data-selection-summary]');
                var total = summary && summary.getAttribute('data-total');
                if (total) {
                    query.push('total=' + encodeURIComponent(total));
                }
            }
        }
        return query.join('&');
    }

    function highlight(node) {
        clearHighlight();
        if (node) {
            node.classList.add(OPTIONS.targetClass);
            node.setAttribute('data-inspector-highlighted', 'true');
        }
    }

    function clearHighlight() {
        var marked = document.querySelectorAll('[' + OPTIONS.targetClass + '], [data-inspector-highlighted]');
        Array.prototype.forEach.call(marked, function (node) {
            node.classList.remove(OPTIONS.targetClass);
            node.removeAttribute('data-inspector-highlighted');
        });
    }

    function field(label, value, status, note) {
        var row = document.createElement('div');
        row.className = 'inspector-field';
        var name = document.createElement('span');
        name.className = 'inspector-label';
        text(name, label);
        var content = document.createElement('span');
        content.className = 'inspector-value' + (status && status !== 'resolved'
            ? ' inspector-value-unknown' : '');
        if (status) {
            content.setAttribute('data-inspector-status', status);
        }
        text(content, value);
        row.appendChild(name);
        row.appendChild(content);
        if (note) {
            var extra = document.createElement('span');
            extra.className = 'inspector-note';
            text(extra, note);
            row.appendChild(extra);
        }
        return row;
    }

    function render(payload) {
        var body = document.querySelector('[data-inspector-fields]');
        var empty = document.querySelector('[data-inspector-empty]');
        var error = document.querySelector('[data-inspector-error]');
        var problems = document.querySelector('[data-inspector-problems]');
        if (!body) {
            return;
        }
        body.replaceChildren();
        if (empty) {
            empty.classList.add('d-none');
        }
        if (error) {
            error.classList.add('d-none');
            text(error, '');
        }
        if (problems) {
            problems.replaceChildren();
            problems.classList.add('d-none');
        }
        (payload.fields || []).forEach(function (entry) {
            body.appendChild(field(entry.label,
                                   entry.value || entry.status_text || '',
                                   entry.status, entry.note));
        });
        if (problems && payload.problems && payload.problems.length) {
            payload.problems.forEach(function (problem) {
                var item = document.createElement('li');
                text(item, problem);
                problems.appendChild(item);
            });
            problems.classList.remove('d-none');
        }
    }

    function showEmpty() {
        var body = document.querySelector('[data-inspector-fields]');
        var empty = document.querySelector('[data-inspector-empty]');
        var error = document.querySelector('[data-inspector-error]');
        var problems = document.querySelector('[data-inspector-problems]');
        if (body) {
            // Never the previous element's answers: an inspector describing the
            // wrong control is worse than one describing nothing.
            body.replaceChildren();
        }
        if (problems) {
            problems.replaceChildren();
            problems.classList.add('d-none');
        }
        if (error) {
            error.classList.add('d-none');
        }
        if (empty) {
            empty.classList.remove('d-none');
        }
    }

    function showError(message) {
        var body = document.querySelector('[data-inspector-fields]');
        var empty = document.querySelector('[data-inspector-empty]');
        var error = document.querySelector('[data-inspector-error]');
        if (body) {
            body.replaceChildren();
        }
        if (empty) {
            empty.classList.add('d-none');
        }
        if (error) {
            error.classList.remove('d-none');
            text(error, message);
        }
    }

    function setLoading(on) {
        STATE.loading = on;
        var node = panel();
        if (node) {
            node.setAttribute('aria-busy', on ? 'true' : 'false');
        }
    }

    function open(node) {
        var element = candidate(node);
        if (!element) {
            return Promise.resolve(null);
        }
        var surface = panel();
        if (!surface) {
            return Promise.resolve(null);
        }
        if (!STATE.active || STATE.active !== element) {
            showEmpty();
            STATE.active = element;
        }
        highlight(element);
        if (surface.classList.contains('d-none')) {
            if (document.activeElement) {
                STATE.returnFocusTo = document.activeElement;
            }
            surface.classList.remove('d-none');
        }
        return Promise.resolve();
    }

    function close(options) {
        var surface = panel();
        if (surface) {
            surface.classList.add('d-none');
        }
        clearHighlight();
        showEmpty();
        STATE.active = null;
        if (!options || options.restoreFocus !== false) {
            var back = STATE.returnFocusTo;
            STATE.returnFocusTo = null;
            if (back && back.focus) {
                back.focus();
            }
        }
    }

    /** Ask the server what this element is. One read-only GET, no more. */
    function inspect(node) {
        var element = candidate(node) || STATE.active;
        if (!element) {
            return Promise.resolve(null);
        }
        var url = OPTIONS.endpoint + '?' + describe(element);
        var ticket = ++STATE.request;
        setLoading(true);
        announce(null, ANNOUNCED.loading);
        return window.fetch(url, {
            method: 'GET',
            headers: {'Accept': 'application/json'},
            credentials: 'same-origin',
        }).then(function (response) {
            return response.json().then(function (payload) {
                return {ok: response.ok && payload.success !== false, payload: payload};
            });
        }).then(function (answer) {
            // A later click wins: an answer about the element inspected two
            // clicks ago must never overwrite the current one.
            if (ticket !== STATE.request) {
                return null;
            }
            setLoading(false);
            if (!answer.ok) {
                var reason = (answer.payload && answer.payload.error)
                    || ANNOUNCED.failed;
                showError(reason);
                announce(element, ANNOUNCED.failed);
                return null;
            }
            render(answer.payload.panel || {});
            announce(element, 'Inspecting ' + (answer.payload.inspection
                && answer.payload.inspection.interface_id || 'the screen') + '.');
            return answer.payload;
        }, function () {
            if (ticket !== STATE.request) {
                return null;
            }
            setLoading(false);
            showError(ANNOUNCED.failed);
            announce(element, ANNOUNCED.failed);
            return null;
        });
    }

    function select(element) {
        return open(element).then(function () {
            return inspect(element);
        });
    }

    function onPointerOver(event) {
        if (!STATE.mode) {
            return;
        }
        var node = candidate(event.target);
        if (!node) {
            return;
        }
        highlight(node);
    }

    function onPointerOut(event) {
        if (!STATE.mode) {
            return;
        }
        if (panel() && panel().contains(event.target)) {
            return;
        }
        if (STATE.active) {
            highlight(STATE.active);
        } else {
            clearHighlight();
        }
    }

    function onClick(event) {
        if (!STATE.mode) {
            return;
        }
        var node = candidate(event.target);
        if (!node) {
            return;
        }
        // The click is the inspection, not the operation: nothing may be
        // deleted, opened or exported by looking at it.
        event.preventDefault();
        event.stopPropagation();
        select(node);
    }

    function onKeyDown(event) {
        if (event.key === 'Escape') {
            if (STATE.active) {
                close();
                announce(null, ANNOUNCED.mode_on);
                return;
            }
            if (STATE.mode) {
                setMode(false);
            }
            return;
        }
        if (!STATE.mode) {
            return;
        }
        if (event.key === 'Enter' || event.key === ' ') {
            var node = candidate(document.activeElement);
            if (node && !(panel() && panel().contains(node))) {
                event.preventDefault();
                select(node);
            }
        }
    }

    function setMode(on) {
        var surface = root();
        STATE.mode = !!on;
        if (surface) {
            surface.setAttribute('data-inspector-active', STATE.mode ? 'true' : 'false');
        }
        var toggle = document.querySelector(OPTIONS.toggle);
        if (toggle) {
            toggle.setAttribute('aria-pressed', STATE.mode ? 'true' : 'false');
        }
        if (!STATE.mode) {
            close({restoreFocus: false});
            clearHighlight();
        }
        announce(null, STATE.mode ? ANNOUNCED.mode_on : ANNOUNCED.mode_off);
    }

    function onToggle(event) {
        if (event && event.preventDefault) {
            event.preventDefault();
        }
        setMode(!STATE.mode);
    }

    function install(options) {
        Object.keys(options || {}).forEach(function (key) {
            if (Object.prototype.hasOwnProperty.call(OPTIONS, key)) {
                OPTIONS[key] = options[key];
            }
        });

        // Idempotent on purpose: a page, a page module and a test may all call
        // `init()`, and one click must not become three inspections.
        if (STATE.initialised) {
            return STATE.instance;
        }
        if (!root()) {
            return null;
        }
        STATE.initialised = true;

        document.addEventListener('mouseover', onPointerOver, true);
        document.addEventListener('mouseout', onPointerOut, true);
        document.addEventListener('click', onClick, true);
        document.addEventListener('keydown', onKeyDown, true);

        var toggle = document.querySelector(OPTIONS.toggle);
        if (toggle) {
            toggle.addEventListener('click', onToggle);
        }
        var closer = document.querySelector('[data-inspector-close]');
        if (closer) {
            closer.addEventListener('click', function (event) {
                event.preventDefault();
                close();
            });
        }
        var surface = panel();
        if (surface) {
            surface.addEventListener('keydown', function (event) {
                // Focus stays inside the panel while it is open, and the way
                // out is Escape: Tab must not walk behind a panel that is
                // describing the thing you are trying to look at.
                if (event.key !== 'Tab') {
                    return;
                }
                var focusable = surface.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
                if (!focusable.length) {
                    return;
                }
                var first = focusable[0];
                var last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            });
        }

        STATE.instance = {
            enabled: function () { return STATE.mode; },
            toggle: setMode,
            open: open,
            close: close,
            inspect: inspect,
            select: select,
            active: function () { return STATE.active; },
            describe: describe,
        };
        return STATE.instance;
    }

    window.ScreenInspector = {
        init: function (options) {
            var instance = install(options);
            var surface = root();
            if (instance && surface
                    && surface.getAttribute('data-inspector-enabled') === 'true') {
                // The installation switched inspection on: it starts in the
                // mode, with the panel closed and nothing selected.
                setMode(true);
            }
            return instance;
        },
        enable: function () { setMode(true); },
        disable: function () { setMode(false); },
        isEnabled: function () { return STATE.mode; },
        open: open,
        close: close,
        inspect: inspect,
        active: function () { return STATE.active; },
        // The only description helper a page or a harness may need: pure, and
        // the same one the click path uses, so nothing here can diverge from
        // what the server is actually asked.
        describe: describe,
        initialised: function () { return STATE.initialised; },
        _state: STATE,
    };
}(window, document));
