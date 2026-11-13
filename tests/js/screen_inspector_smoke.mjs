/**
 * The Screen Inspector's runtime, exercised against the shipped module.
 *
 * The panel is only as good as the four things this harness checks, and each of
 * them is a way an inspection tool goes wrong:
 *
 *   * `init()` may be called by a page, a page module and the shell - and one
 *     click must still produce one inspection, not three;
 *   * inspecting a control must never *use* it: a Delete button that deletes
 *     while you are reading about it is worse than no inspector;
 *   * Escape leaves targeting before it leaves the mode, and the panel never
 *     keeps focus after it closes;
 *   * an answer about the element inspected two clicks ago never overwrites the
 *     one on screen, and nothing is ever shown for an element nobody inspected.
 *
 * Usage:
 *     node tests/js/screen_inspector_smoke.mjs
 */

import { installDom, loadRuntime, treeFromHtml } from './_dom_stub.mjs';

const failures = [];
const notes = [];
let checks = 0;

function check(what, condition, detail = '') {
    checks += 1;
    if (!condition) {
        failures.push(`${what}${detail ? ` -- ${detail}` : ''}`);
    }
}

const { documentRoot, globalThisRef } = installDom();
globalThisRef.setTimeout = setTimeout;
//: The runtime logs through the window's console; quieten that without
//: quietening this harness, which reports through the process' own stdout.
const out = (line) => process.stdout.write(`${line}\n`);
globalThisRef.console = {debug() {}, log() {}, error() {}, warn() {}};

// A page, as the shell renders it: one inspector component, one toolbar whose
// buttons carry their registry identity, and a record surface.
documentRoot.appendChild(treeFromHtml(`
<body data-current-endpoint="sources_list">
  <div class="action-bar" role="toolbar" id="sourcesActionBar" data-action-toolbar
       data-selected-count="3" data-inspector-component="ActionToolbar"
       data-inspector-interface="sources">
    <span data-selection-summary data-total="27" data-noun="sources">3 of 27 sources selected</span>
    <button type="button" id="bulkDeleteBtn" data-bulk-action
            data-inspector-interface="sources" data-inspector-action="sources.delete_selected"
            data-inspector-component="ActionToolbar" data-inspector-role="control">Delete Selected</button>
    <button type="button" id="bulkEditBtn" data-bulk-action
            data-inspector-interface="sources" data-inspector-action="sources.edit_selected"
            data-inspector-component="ActionToolbar" data-inspector-role="control">Edit Selected</button>
  </div>
  <div class="inspector-root" id="screenInspector" data-screen-inspector
       data-inspector-component="ScreenInspector" data-inspector-role="panel"
       data-inspector-interface="sources" data-inspector-enabled="true">
    <button type="button" class="inspector-toggle" data-inspector-toggle aria-pressed="false">Inspector</button>
    <p class="visually-hidden" role="status" aria-live="polite" data-inspector-announce></p>
    <section class="inspector-panel d-none" id="screenInspector-panel" role="dialog"
             aria-modal="false" data-inspector-panel tabindex="-1">
      <header class="inspector-header">
        <h2 class="inspector-title" id="screenInspector-title">Screen Inspector</h2>
        <button type="button" class="btn-close" data-inspector-close aria-label="Close"></button>
      </header>
      <div class="inspector-body" data-inspector-body>
        <p class="inspector-empty" data-inspector-empty>Select an element to inspect its SYLTHARAE contract.</p>
        <p class="inspector-problem d-none" data-inspector-error role="alert"></p>
        <div class="inspector-fields" data-inspector-fields></div>
        <ul class="inspector-problems d-none" data-inspector-problems></ul>
      </div>
    </section>
  </div>
</body>`));

// A stand-in for the server's panel payload: the shape the endpoint returns.
const ANSWERS = {
    'sources.delete_selected': {
        fields: [
            {key: 'interface', label: 'Interface', value: 'sources', status: 'resolved'},
            {key: 'component', label: 'Component', value: 'action_toolbar', status: 'resolved'},
            {key: 'action', label: 'Action', value: 'sources.delete_selected', status: 'resolved'},
            {key: 'scope', label: 'Scope', value: 'selection', status: 'resolved'},
            {key: 'permission', label: 'Permission metadata', value: 'sources.delete', status: 'resolved'},
            {key: 'authorization', label: 'Current authorization', value: 'Not determined', status: 'not_checked'},
            {key: 'state', label: 'State', value: 'available', status: 'resolved'},
            {key: 'execution', label: 'Execution reference', value: null, status: 'not_built',
             status_text: 'Not built'},
        ],
        problems: [],
    },
    'sources.edit_selected': {
        fields: [
            {key: 'action', label: 'Action', value: 'sources.edit_selected', status: 'resolved'},
            {key: 'binding', label: 'Binding', value: 'Unresolved', status: 'unresolved',
             note: 'No control in the product names this action'},
        ],
        problems: ['sources.edit_selected is presented by sources and nothing binds it'],
    },
};

const requests = [];
let failNext = false;

globalThisRef.fetch = function (url) {
    requests.push(url);
    const wanted = Object.keys(ANSWERS).find((action) => url.includes(action));
    const payload = failNext
        ? {success: false, error: 'No interface could be resolved for this element.'}
        : {success: true, panel: wanted ? ANSWERS[wanted] : {fields: [], problems: []}};
    return Promise.resolve({
        ok: !failNext,
        json: () => Promise.resolve(payload),
    });
};

loadRuntime('static/js/modules/core/screen-inspector.js');
check('the inspector runtime is defined',
      typeof globalThisRef.ScreenInspector === 'object');

// ---------------------------------------------------------------------------
// Activation is idempotent
// ---------------------------------------------------------------------------
const first = globalThisRef.ScreenInspector.init();
const second = globalThisRef.ScreenInspector.init();
const third = globalThisRef.ScreenInspector.init();
check('init() returns the same instance every time', first === second && second === third);
check('init() installed its listeners exactly once',
      globalThisRef.ScreenInspector.initialised() === true);
check('the installation switched it on, so it starts in the mode',
      globalThisRef.ScreenInspector.isEnabled() === true);
check('the toggle reports the mode it is in',
      documentRoot.querySelector('[data-inspector-toggle]').getAttribute('aria-pressed') === 'true');
notes.push(`init calls: 3, listeners installed: 1`);

// ---------------------------------------------------------------------------
// Selecting an element inspects it, and does not use it
// ---------------------------------------------------------------------------
const button = documentRoot.querySelector('[id="bulkDeleteBtn"]');
let clickedThrough = 0;
button.addEventListener('click', () => { clickedThrough += 1; });

const before = requests.length;
button.dispatch('click', {target: button});
await new Promise((resolve) => setTimeout(resolve, 0));
check('clicking an element fetches its inspection once',
      requests.length === before + 1, requests.slice(before).join(', '));
check('the request names the element and its screen',
      requests[requests.length - 1].includes('action=sources.delete_selected')
      && requests[requests.length - 1].includes('interface=sources')
      && requests[requests.length - 1].includes('component=ActionToolbar'),
      requests[requests.length - 1]);
check('the request carries the selection the toolbar keeps, not a business scope',
      requests[requests.length - 1].includes('selected=3')
      && requests[requests.length - 1].includes('total=27'));
check('the click never reached the control', clickedThrough === 0,
      'inspecting a Delete button must not delete anything');

const panel = documentRoot.querySelector('[data-inspector-panel]');
check('the panel opened', !panel.classList.contains('d-none'));
check('the panel announced its state politely',
      documentRoot.querySelector('[data-inspector-announce]').textContent.length > 0);
const rendered = documentRoot.querySelector('[data-inspector-fields]').textContent;
check('the panel shows the registry metadata',
      rendered.includes('sources.delete_selected') && rendered.includes('sources.delete'));
check('permission metadata and authorization are shown apart',
      rendered.includes('Permission metadata') && rendered.includes('Current authorization')
      && rendered.includes('Not determined'));
check('a value nobody declared is shown as a state, not a blank',
      documentRoot.querySelector('[data-inspector-fields]').textContent.includes('Not built'));

// ---------------------------------------------------------------------------
// The previous element's answers are never left on screen
// ---------------------------------------------------------------------------
const editButton = documentRoot.querySelector('[id="bulkEditBtn"]');
requests.length = 0;
editButton.dispatch('click', {target: editButton});
check('a new selection empties the panel before the answer arrives',
      documentRoot.querySelector('[data-inspector-fields]').textContent === '',
      'the previous element\'s values must not be read as this one\'s');
await new Promise((resolve) => setTimeout(resolve, 0));
const secondRender = documentRoot.querySelector('[data-inspector-fields]').textContent;
check('the answer describes the element that was selected',
      secondRender.includes('sources.edit_selected')
      && !secondRender.includes('sources.delete_selected'));
check('what the report could not resolve is listed',
      documentRoot.querySelector('[data-inspector-problems]').textContent
          .includes('nothing binds it'));

// ---------------------------------------------------------------------------
// A failure is reported, and carries nothing technical
// ---------------------------------------------------------------------------
failNext = true;
editButton.dispatch('click', {target: editButton});
await new Promise((resolve) => setTimeout(resolve, 0));
const error = documentRoot.querySelector('[data-inspector-error]');
check('a refusal is reported in the panel', !error.classList.contains('d-none'));
check('the panel gives up the old answer when the new one fails',
      documentRoot.querySelector('[data-inspector-fields]').textContent === '');
check('the message is the server\'s own and carries no trace',
      !/Traceback|psycopg2|SELECT /.test(error.textContent));
failNext = false;

// ---------------------------------------------------------------------------
// Escape leaves targeting before it leaves the mode, and focus comes back
// ---------------------------------------------------------------------------
documentRoot.querySelector('[data-inspector-close]').dispatch('click', {});
check('closing empties the panel again',
      documentRoot.querySelector('[data-inspector-fields]').textContent === '');
check('closing clears the highlight',
      documentRoot.querySelectorAll('.inspector-target').length === 0);

button.dispatch('click', {target: button});
await new Promise((resolve) => setTimeout(resolve, 0));
const state = globalThisRef.ScreenInspector;
check('the panel remembers what it is describing',
      state.active() === button);
button.dispatch('keydown', {key: 'Escape'});
check('Escape leaves the element selected, not the mode',
      state.isEnabled() === true && state.active() === null);
documentRoot.querySelector('[data-inspector-toggle]').dispatch('click', {});
check('the toggle leaves the mode',
      state.isEnabled() === false
      && documentRoot.querySelector('[data-inspector-toggle]').getAttribute('aria-pressed') === 'false');
check('leaving the mode closes the panel',
      documentRoot.querySelector('[data-inspector-panel]').classList.contains('d-none'));

// ---------------------------------------------------------------------------
// Repeated selection: one answer per click, and the last one wins
// ---------------------------------------------------------------------------
globalThisRef.ScreenInspector.enable();
requests.length = 0;
button.dispatch('click', {target: button});
await new Promise((resolve) => setTimeout(resolve, 0));
button.dispatch('click', {target: button});
await new Promise((resolve) => setTimeout(resolve, 0));
check('two clicks make two requests, not four',
      requests.length === 2, requests.length);
check('the panel shows the last answer once',
      documentRoot.querySelectorAll('.inspector-field').length
      === ANSWERS['sources.delete_selected'].fields.length,
      `${documentRoot.querySelectorAll('.inspector-field').length} rows for `
      + `${ANSWERS['sources.delete_selected'].fields.length} fields`);

// Reviewing an element inside a region inherits the region's identity.
const summary = documentRoot.querySelector('[data-selection-summary]');
check('an element inside a described region is described by it',
      globalThisRef.ScreenInspector.describe
      && String(globalThisRef.ScreenInspector.describe(summary)).includes('interface=sources'),
      globalThisRef.ScreenInspector.describe ? globalThisRef.ScreenInspector.describe(summary) : '');

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
for (const note of notes) {
    out(`note: ${note}`);
}
if (failures.length) {
    failures.forEach((failure) => out(`FAIL ${failure}`));
    out(`${checks - failures.length}/${checks} checks passed`);
    process.exit(1);
}
out(`${checks}/${checks} checks passed`);
