/**
 * Previous/Next file keyboard shortcuts (File Detail and Reader pages).
 *
 * Alt+Left / Alt+Right follow the same links the buttons do, which means they
 * inherit the browsing context (filters, list order) carried in the href. The
 * application swap updates the content and history without redrawing the shell.
 *
 * The browser's own Alt+Left / Alt+Right history navigation is deliberately
 * overridden only while a Previous/Next link exists in that direction; with no
 * neighbouring file (or on any other page) the keys are left alone.
 */
(function () {
    'use strict';

    if (window.__inforaxisFileNavBound) {
        return;
    }
    window.__inforaxisFileNavBound = true;

    function isEditingTarget(target) {
        if (!target || !target.closest) {
            return false;
        }
        return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'));
    }

    function navScope() {
        var dock = document.getElementById('fileNavDock');
        return dock || document;
    }

    function linkFor(direction) {
        return navScope().querySelector(
            'a.file-nav__button[data-nav-dir="' + direction + '"]'
        );
    }

    document.addEventListener('keydown', function (event) {
        if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
            return;
        }
        var direction = null;
        if (event.key === 'ArrowLeft') {
            direction = 'prev';
        } else if (event.key === 'ArrowRight') {
            direction = 'next';
        }
        if (!direction || isEditingTarget(event.target)) {
            return;
        }
        var link = linkFor(direction);
        if (link && link.getAttribute('href')) {
            event.preventDefault();
            if (typeof window.swapNavigate === 'function') {
                window.swapNavigate(link.getAttribute('href'));
            } else {
                window.location.assign(link.getAttribute('href'));
            }
        }
    });
})();
