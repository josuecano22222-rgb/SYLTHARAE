/**
 * In-place Extracted/Original content tabs for the full-page viewers.
 *
 * The File Details pop-up (templates/file/File_Management_Analysis_System.html
 * + file-operations/file-details.js) shows the extracted text and the original
 * source file as two tabs over one pane. This module brings the exact same
 * contract to the two full-page viewers — /file/<id> and
 * /file/<id>/full-content — so "view original" happens where the extracted
 * text is, in place, instead of navigating away to a second page/tab.
 *
 * Markup contract (both pages):
 *   [data-content-tab="extracted"|"original"]  — the tab buttons
 *   [data-content-pane="extracted"]            — the extracted-text pane
 *   [data-content-pane="original"]             — the original-file pane
 *   [data-extracted-only]                      — chrome (search, pager, chunk
 *                                                controls) hidden with the
 *                                                extracted pane
 *
 * Rendering of the original pane is delegated to showOriginalFile()
 * (file-operations/original-file.js) — the same code the pop-up uses: the
 * server describes the file, the module picks image / iframe / text /
 * audio / video / download-message, lazily on first switch.
 */

import { showOriginalFile } from './file-operations/original-file.js';

export function initOriginalContentTab(fileId) {
    if (!fileId) return;

    const tabs = Array.from(document.querySelectorAll('[data-content-tab]'));
    const extractedPane = document.querySelector('[data-content-pane="extracted"]');
    const originalPane = document.querySelector('[data-content-pane="original"]');
    if (!tabs.length || !extractedPane || !originalPane) return;

    let loaded = false;

    function setTab(wanted) {
        const target = wanted === 'original' ? 'original' : 'extracted';

        tabs.forEach((tab) => {
            const active = tab.getAttribute('data-content-tab') === target;
            tab.classList.toggle('active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
        });

        extractedPane.style.display = target === 'extracted' ? '' : 'none';
        originalPane.style.display = target === 'original' ? '' : 'none';

        // Search controls, pager, chunk selector … belong to the extracted
        // text exactly as in the pop-up.
        document.querySelectorAll('[data-extracted-only]').forEach((el) => {
            el.style.display = target === 'extracted' ? '' : 'none';
        });

        if (target === 'original' && !loaded) {
            loaded = true;
            showOriginalFile(fileId, originalPane).catch((err) => {
                loaded = false; // allow a retry on the next click
                console.error('Could not render the original file:', err);
            });
        }
    }

    tabs.forEach((tab) => tab.addEventListener('click', () =>
        setTab(tab.getAttribute('data-content-tab'))));
}
