/**
 * status.js — how a status is shown when the page updates without reloading.
 *
 * The vocabulary itself is not here. It is defined once in
 * `core/frontend/status_vocabulary.py` and injected into the page as JSON
 * (see `templates/base.html`), so a status the job engine invents tomorrow is
 * presented by both the server-rendered badge and this renderer without either
 * of them being edited. Before this module the job pages, the job list and the
 * operations widget each carried their own status-to-colour map, and the three
 * disagreed about COMPLETED_WITH_WARNINGS.
 *
 * Rendering only: it does not decide what a status means beyond asking the
 * vocabulary, and it does not fetch anything.
 */
(function (window, document) {
    'use strict';

    var STATE_CLASSES = {
        success: 'bg-success',
        warning: 'bg-warning text-dark',
        danger: 'bg-danger',
        info: 'bg-info text-dark',
        progress: 'bg-primary',
        neutral: 'bg-secondary',
        muted: 'bg-secondary'
    };

    function vocabulary() {
        var tag = document.getElementById('status-vocabulary');
        if (!tag) return {};
        try {
            return JSON.parse(tag.textContent || '{}');
        } catch (error) {
            return {};
        }
    }

    function normalise(status) {
        return String(status == null ? '' : status)
            .trim()
            .replace(/[ \-.]/g, '_')
            .replace(/__+/g, '_')
            .toUpperCase();
    }

    function present(status, label) {
        var entry = vocabulary()[normalise(status)];
        if (entry) {
            return { state: entry.state, label: label || entry.label, known: true };
        }
        var text = String(status == null ? '' : status);
        return {
            state: 'neutral',
            // An unrecognised status keeps its own words and does not borrow a
            // meaning: the interface has no opinion about it.
            label: label || text.replace(/_/g, ' '),
            known: false
        };
    }

    function classFor(state) {
        return STATE_CLASSES[state] || STATE_CLASSES.neutral;
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    /** The badge markup for a status, identical to the Jinja component's. */
    function badge(status, label, extraClass) {
        var info = present(status, label);
        return '<span class="badge ' + classFor(info.state) +
            (extraClass ? ' ' + extraClass : '') + '">' +
            escapeHtml(info.label) + '</span>';
    }

    /** Replace the contents of a container with the badge for a status. */
    function render(element, status, label) {
        if (!element) return;
        element.innerHTML = badge(status, label);
        element.setAttribute('data-state', present(status).state);
    }

    window.InforaxisStatus = {
        present: present,
        badge: badge,
        render: render,
        classFor: classFor
    };
}(window, document));
