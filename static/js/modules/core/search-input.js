/**
 * Search input: the behaviour the search box component deliberately does not
 * put in markup.
 *
 * The component renders the box, the clear control and the hooks; this module
 * adds the conventions that must be the same wherever a search box appears:
 *
 * - Escape clears the box through the page's own clear control, so the page's
 *   handler runs once and nothing is duplicated. If there is no clear control,
 *   the value is emptied and an `input` event is dispatched so whatever the
 *   page listens to runs exactly as if the reader had cleared it by hand.
 * - `setLoading(id, true)` shows that a search is in flight and marks the box
 *   busy; `setLoading(id, false)` says it finished. A search that never
 *   finishes is visible rather than silent.
 *
 * It does not know what a search means: no URLs, no endpoints, no queries.
 */
(function () {
    'use strict';

    function clearBox(input) {
        var wrapper = input.closest('[data-search-input]');
        var button = wrapper && wrapper.querySelector('.btn-clear-search');
        if (button && !button.disabled) {
            button.click();
            return;
        }
        if (input.value === '') return;
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape' && event.key !== 'Esc') return;
        var input = event.target;
        if (!input || !input.classList || !input.classList.contains('search-input')) return;
        clearBox(input);
    });

    function setLoading(id, loading) {
        var input = document.getElementById(id);
        if (!input) return;
        var wrapper = input.closest('[data-search-input]');
        if (!wrapper) return;
        var spinner = wrapper.querySelector('.search-input-spinner');
        if (loading) {
            input.setAttribute('aria-busy', 'true');
            if (spinner) spinner.classList.remove('d-none');
        } else {
            input.removeAttribute('aria-busy');
            if (spinner) spinner.classList.add('d-none');
        }
    }

    window.InforaxisSearch = {
        setLoading: setLoading,
        clear: function (id) {
            var input = document.getElementById(id);
            if (input) clearBox(input);
        }
    };
})();
