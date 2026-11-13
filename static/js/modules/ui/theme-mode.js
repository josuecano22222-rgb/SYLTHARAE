/**
 * Theme mode: light, dark, or whatever the operating system says.
 *
 * The application already has a colour-token layer (`--bg-white`,
 * `--text-dark`, …) that a user can override from Settings, but it has only
 * ever had one mode. This module adds the second one and keeps it honest:
 *
 *  - **auto** (the default) follows `prefers-color-scheme` and follows it
 *    *live* — changing the OS setting re-themes an open page;
 *  - **light** and **dark** pin the mode and win over the OS;
 *  - the choice is remembered in `localStorage` and applied to `<html>` as
 *    `data-theme-mode`, before first paint (see `theme-mode-init.js`, which is
 *    loaded synchronously in the head for exactly that reason).
 *
 * `data-theme-mode` is the switch. `theme-mode.css` is the only stylesheet that
 * writes to it, so a page that never loads this module still renders in the
 * light theme instead of half-and-half.
 *
 * The event `theme-mode-changed` carries `{ mode, resolved }` so a canvas
 * chart can redraw with the right ink colour instead of guessing.
 */

const STORAGE_KEY = 'syltharae-theme-mode';
export const MODES = ['auto', 'light', 'dark'];
const QUERY = '(prefers-color-scheme: dark)';

function readStored() {
    try {
        const value = window.localStorage.getItem(STORAGE_KEY);
        return MODES.includes(value) ? value : 'auto';
    } catch (error) {
        // Private mode / disabled storage: auto is the honest default.
        return 'auto';
    }
}

function systemPrefersDark() {
    return typeof window.matchMedia === 'function' && window.matchMedia(QUERY).matches;
}

function resolve(mode) {
    if (mode === 'light' || mode === 'dark') return mode;
    return systemPrefersDark() ? 'dark' : 'light';
}

/** Apply a mode (does not persist it). Returns the resolved mode. */
export function applyMode(mode, { persist = true } = {}) {
    const chosen = MODES.includes(mode) ? mode : 'auto';
    const resolved = resolve(chosen);
    const root = document.documentElement;
    root.setAttribute('data-theme-mode', resolved);
    root.setAttribute('data-theme-preference', chosen);
    root.style.colorScheme = resolved;
    if (persist) {
        try {
            window.localStorage.setItem(STORAGE_KEY, chosen);
        } catch (error) {
            /* the mode still applies to this page */
        }
    }
    document.dispatchEvent(new CustomEvent('theme-mode-changed', {
        detail: { mode: chosen, resolved },
    }));
    return resolved;
}

export function currentMode() {
    return readStored();
}

export function currentResolved() {
    return resolve(readStored());
}

/** Cycle the way a menu or a shortcut wants: auto → light → dark → auto. */
export function cycleMode() {
    const order = ['auto', 'light', 'dark'];
    const next = order[(order.indexOf(readStored()) + 1) % order.length];
    return { mode: next, resolved: applyMode(next) };
}

const LABELS = { auto: 'Auto', light: 'Light', dark: 'Dark' };

/** Keep the account-menu control in step with the stored preference. */
function paintMenu(mode) {
    const badge = document.getElementById('themeModeBadge');
    if (badge) {
        const text = (badge.dataset.labels && JSON.parse(badge.dataset.labels)[mode]) || LABELS[mode];
        badge.textContent = text;
    }
    document.querySelectorAll('[data-theme-choice]').forEach((button) => {
        const active = button.dataset.themeChoice === mode;
        button.setAttribute('aria-pressed', String(active));
        button.classList.toggle('active', active);
        button.classList.toggle('btn-primary', active);
        button.classList.toggle('btn-outline-secondary', !active);
    });
}

/** The account-menu control: a toggle that reveals three choices. */
function wireMenu() {
    const trigger = document.getElementById('themeModeItem');
    const choices = document.getElementById('themeModeChoices');
    if (trigger && choices && !trigger.dataset.wired) {
        trigger.dataset.wired = '1';
        trigger.addEventListener('click', (event) => {
            event.preventDefault();
            choices.hidden = !choices.hidden;
            trigger.setAttribute('aria-expanded', String(!choices.hidden));
        });
    }
    document.querySelectorAll('[data-theme-choice]').forEach((button) => {
        if (button.dataset.wired) return;
        button.dataset.wired = '1';
        button.addEventListener('click', () => {
            applyMode(button.dataset.themeChoice);
            paintMenu(button.dataset.themeChoice);
        });
    });
}

let listening = false;

export function initThemeMode() {
    applyMode(readStored(), { persist: false });
    wireMenu();
    paintMenu(readStored());
    document.addEventListener('theme-mode-changed', (event) => {
        paintMenu(event.detail.mode);
    });
    if (listening || typeof window.matchMedia !== 'function') return;
    listening = true;
    const media = window.matchMedia(QUERY);
    const onChange = () => {
        // Only auto follows the system; an explicit choice is a decision.
        if (readStored() === 'auto') applyMode('auto', { persist: false });
    };
    if (typeof media.addEventListener === 'function') {
        media.addEventListener('change', onChange);
    } else if (typeof media.addListener === 'function') {
        media.addListener(onChange); // older Safari
    }
}

const api = { MODES, applyMode, currentMode, currentResolved, cycleMode, initThemeMode };
if (typeof window !== 'undefined') {
    window.ThemeMode = api;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeMode, { once: true });
    } else {
        initThemeMode();
    }
}

export default api;
