/**
 * Action toolbar - runtime presentation.
 *
 * The component renders the bar and the state that is known at render time:
 * nothing is selected, so the selection-scoped actions are disabled and the
 * scope element says what selection is needed. This module is what keeps the
 * scope honest once the reader starts selecting:
 *
 *     ActionToolbar.sync('itemsActionBar', {
 *         selected: 3,
 *         total: 27
 *     });
 *
 * `sync` is the whole public API, and it is the whole of what the toolbar
 * knows. It never learns what an action does - there is no `exportSelected`,
 * no `delete`, no endpoint and no `fetch` anywhere in this file. The page
 * counts the selection, performs the action and handles the outcome; the
 * toolbar decides how that state is drawn and announced.
 *
 * What `sync` writes, for every `[data-bulk-action]` button in the bar:
 *
 *   selected = 0   -> disabled, "Export Selected: Select sources first"
 *   selected = 1   -> enabled,  "Export Selected: 1 source selected"
 *   selected = 3   -> enabled,  "Export Selected: 3 sources selected"
 *   all selected   -> enabled,  "Export Selected: 27 of 27 sources selected"
 *
 * `selected` and `total` are the page's numbers - `total` is what the page is
 * offering, not the server's estimate - and the words are the bar's. The
 * sentences are phrase templates the component rendered through the translation
 * catalogs, so no English is invented here; `sync` only fills in {count},
 * {total} and {noun}. A page may pass `noun`/`nounSingular` of its own; when
 * there is no noun at all there is no sentence to fill in, and the module
 * updates the enabled/disabled state without touching the wording rather than
 * inventing one.
 *
 * It is idempotent and it binds nothing: calling `sync` twice with the same
 * numbers leaves the same DOM, and no event listener is registered here, so a
 * page cannot double-update by syncing more than once.
 */
(function (window, document) {
    'use strict';

    function element(root) {
        return typeof root === 'string' ? document.getElementById(root) : root;
    }

    function attribute(node, name) {
        return node && node.getAttribute ? (node.getAttribute(name) || '') : '';
    }

    function fill(template, values) {
        return String(template).replace(/\{(\w+)\}/g, function (match, key) {
            return values[key] === undefined ? match : values[key];
        });
    }

    function count(value) {
        var number = parseInt(value, 10);
        return isNaN(number) || number < 0 ? 0 : number;
    }

    function scopeText(root, summary, selected, total, noun, nounSingular) {
        if (!noun) {
            // Nothing to say in the reader's language, so say nothing: the
            // server's own sentence stays where it is.
            return null;
        }
        var words = selected === 1 ? (nounSingular || noun) : noun;
        if (selected === 0) {
            return fill(attribute(root, 'data-text-select-first'), { noun: noun });
        }
        if (total !== null && selected <= total) {
            var whole = total === 1 ? (nounSingular || noun) : noun;
            if (selected === total) {
                return fill(attribute(root, 'data-text-all-selected'),
                            { count: selected, total: total, noun: whole });
            }
        }
        return fill(attribute(root, 'data-text-selected'),
                    { count: selected, noun: words });
    }

    /**
     * Tell the toolbar how much is selected. The page owns the number.
     *
     * @param {Element|string} root the bar, or its id
     * @param {{selected: number, total?: number|string, noun?: string,
     *          nounSingular?: string}} spec
     * @returns {string|null} the sentence now shown, or null when none was written
     */
    function sync(root, spec) {
        var bar = element(root);
        if (!bar) return null;

        spec = spec || {};
        var summary = bar.querySelector ? bar.querySelector('[data-selection-summary]') : null;
        var selected = count(spec.selected);

        var total = spec.total;
        if (total === undefined || total === null || total === '') {
            total = attribute(summary, 'data-total');
        }
        total = total === '' ? null : count(total);

        var noun = spec.noun || attribute(summary, 'data-noun');
        var nounSingular = spec.nounSingular || attribute(summary, 'data-noun-singular');
        var sentence = scopeText(bar, summary, selected, total, noun, nounSingular);

        if (summary && sentence !== null) {
            summary.textContent = sentence;
        }
        if (summary) {
            summary.setAttribute('data-selected-count', String(selected));
        }

        var buttons = bar.querySelectorAll ? bar.querySelectorAll('[data-bulk-action]') : [];
        Array.prototype.forEach.call(buttons, function (button) {
            var label = attribute(button, 'data-action-label');
            var name = sentence === null ? null
                : (label ? label + ': ' + sentence : sentence);
            button.disabled = selected === 0;
            if (selected === 0) {
                button.setAttribute('aria-disabled', 'true');
            } else {
                button.removeAttribute('aria-disabled');
            }
            if (name !== null) {
                button.setAttribute('aria-label', name);
                button.setAttribute('title', name);
            }
        });

        // One select/deselect control: Select All when empty, Select None (+
        // count) when any row is chosen. Replaces the old Select All / Select
        // None pair without teaching the toolbar what selection means.
        var toggle = bar.querySelector ? bar.querySelector('[data-select-toggle]') : null;
        if (toggle) {
            var active = selected > 0;
            var toggleLabel = active
                ? (attribute(toggle, 'data-text-select-none') || 'Select None')
                : (attribute(toggle, 'data-text-select-all') || 'Select All');
            toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
            toggle.classList.toggle('is-selected', active);
            toggle.setAttribute('aria-label', toggleLabel);
            toggle.setAttribute('title', toggleLabel);
            var labelNode = toggle.querySelector('.select-toggle-label');
            if (labelNode) {
                labelNode.textContent = toggleLabel;
            }
            var iconNode = toggle.querySelector('.select-toggle-icon');
            if (iconNode) {
                iconNode.className = 'bi ' +
                    (active ? 'bi-dash-square-fill' : 'bi-check-square-fill') +
                    ' select-toggle-icon';
            }
            var countNode = toggle.querySelector('[data-select-toggle-count]');
            if (countNode) {
                countNode.textContent = String(selected);
                if (active) {
                    countNode.removeAttribute('hidden');
                } else {
                    countNode.setAttribute('hidden', '');
                }
            }
        }

        bar.setAttribute('data-selected-count', String(selected));
        return sentence;
    }

    window.ActionToolbar = { sync: sync };
}(window, document));
