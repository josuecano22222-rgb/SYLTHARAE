/**
 * Jump-to-page wiring for the numbered pager (server-rendered and client-rendered).
 *
 * Load More is NOT handled here: the pager's one Load More control is the table
 * engine's (static/js/modules/ui/unified-table.js), which owns its clicks, its
 * busy state, its errors and its counters. This file only wires the jump input.
 *
 * Wires every ``.unified-pagination-jump-btn`` inside a root. Safe to run more
 * than once (each control is tagged after wiring). Soft navigation swaps
 * ``#mainContent`` without re-executing this file, so it also re-wires on
 * ``syltharae:soft-nav``.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01).
 */
(function (window, document) {
    'use strict';

    function wireJumpButtons(root) {
        root.querySelectorAll('.unified-pagination-jump-btn[data-endpoint]').forEach(function (jumpBtn) {
            if (jumpBtn.hasAttribute('data-jump-wired')) return;
            var jumpInput = document.getElementById(
                'jumpToPageInput-' + jumpBtn.getAttribute('data-endpoint'));
            if (!jumpInput) return;
            jumpBtn.setAttribute('data-jump-wired', '');

            var handleJump = function () {
                var targetPage = parseInt(jumpInput.value, 10);
                var totalPages = parseInt(jumpInput.getAttribute('data-total-pages'), 10) || 1;

                if (isNaN(targetPage) || targetPage < 1) {
                    targetPage = 1;
                } else if (targetPage > totalPages) {
                    targetPage = totalPages;
                }

                var url = new URL(window.location.href);
                url.searchParams.set('page', targetPage);
                if (window.swapNavigate) { window.swapNavigate(url.toString()); }
                else { window.location.href = url.toString(); }
            };

            jumpBtn.addEventListener('click', handleJump);
            jumpInput.addEventListener('keypress', function (e) {
                if (e.key === 'Enter') {
                    handleJump();
                }
            });
        });
    }

    /**
     * Wire the jump control under ``root`` (defaults to document).
     * Idempotent: already-wired controls are skipped.
     */
    function initUnifiedPaginationControls(root) {
        var scope = root && root.querySelectorAll ? root : document;
        wireJumpButtons(scope);
    }

    window.initUnifiedPaginationControls = initUnifiedPaginationControls;

    initUnifiedPaginationControls(document);

    document.addEventListener('syltharae:soft-nav', function () {
        initUnifiedPaginationControls(document.getElementById('mainContent') || document);
    });
}(window, document));
