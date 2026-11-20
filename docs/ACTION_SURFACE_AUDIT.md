# Action surface audit

Every screen action the product exposes, and where each one is registered.

The block below is generated:

```bash
python3 -m core.experience.action_audit docs/ACTION_SURFACE_AUDIT.md
```

This audit is **not the Action Registry**. The registry declares what an action
is and is the source of truth; this document is a survey of what is declared and
of the places an action is still wired by hand. Where the two disagree, the
registry wins and this document is regenerated.

<!-- BEGIN GENERATED: action surfaces -->
### What was measured

Four sources, none of them typed by hand:

* the **Action Registry** - every action the product offers, with the scope, permission, confirmation and operation reference it declares;
* the **surfaces the templates carry** - the shared toolbar, the bars still written by hand, record-row actions, browser confirmation dialogs, viewer controls and page-local button state, each with the files that prove it;
* **what the registry is missing** - actions with no operation behind them, and screens nobody has described;
* the **gaps** - actions whose behaviour the current model cannot say, each anchored to code. A gap that closes leaves this list; two already have.

This is a measurement of the product as it is, not a target.

| Measure | Value |
| --- | --- |
| Registered interfaces | 35 |
| Navigable screens | 34 |
| Screens with a described experience | 5 |
| Screens nobody has described yet | 29 |
| Registered actions | 34 |
| - page / record / selection / bulk | 13 / 10 / 2 / 9 |
| - destructive | 6 |
| - naming a confirmation | 7 |
| - naming a permission | 34 |
| - naming the operation that owns them | 29 |
| - declared with no operation behind them | 5 |
| Templates rendering the shared ActionToolbar | 5 |
| Hand-written action bars | 1 |
| Templates with record actions drawn by hand | 3 |
| Templates with a filter submit inside a bar | 1 |
| Templates with document viewer controls | 1 |
| Files still calling the browser confirm() | 24 |
| Files deciding button state by hand | 15 |
| Gaps: actions the model cannot describe | 12 |

### Registered actions

Every action names a permission domain and either an operation or nothing at all. The empty operation column is the honest part: 5 of them are words on a screen that no service owns yet.

Declared with no operation: `files.reprocess, sources.edit_selected, sources.export_selected, sides.edit_selected, sides.export_selected`.

| Action | Interface | Scope | Selection | Permission | Destructive | Confirmation | Operation | Component |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| files.select_all | file_library | page | - | files.view | - | - | select_all | hand-written bar |
| files.select_none | file_library | page | - | files.view | - | - | select_none | hand-written bar |
| files.upload | file_library | page | - | files.upload | - | - | upload_files | hand-written bar |
| files.open_record | file_library | record | - | files.view | - | - | view_details | record row |
| files.analyze_selected | file_library | bulk | all | files.analyze | - | - | analyze_selected | hand-written bar |
| files.export_selected | file_library | bulk | all | files.export | - | - | export_selected | hand-written bar |
| files.delete_selected | file_library | bulk | all | files.delete | yes | action.files.delete_selected.confirm | delete_selected | hand-written bar |
| files.delete | file_library | record | - | files.delete | yes | action.files.delete.confirm | delete_file | record row |
| files.reprocess | file_library | record | - | files.reprocess | - | - | **none** | record row |
| files.view_original | file_library | record | - | files.view_original | - | - | view_original | record row |
| files.export_content | file_library | record | - | files.export | - | - | export_file_content | record row |
| files.download_original | file_library | record | - | files.download_original | - | - | download_original | record row |
| keywords.select_all | keywords | page | - | keywords.view | - | - | select_all | ActionToolbar |
| keywords.select_none | keywords | page | - | keywords.view | - | - | select_none | ActionToolbar |
| keywords.update | keywords | page | - | keywords.edit | - | - | update_keywords | ActionToolbar |
| keywords.edit_selected | keywords | selection | one | keywords.edit | - | - | edit_keyword | ActionToolbar |
| keywords.delete_selected | keywords | bulk | all | keywords.delete | yes | action.keywords.delete_selected.confirm | delete_keywords | ActionToolbar |
| keywords.merge_duplicates | keywords | page | - | keywords.delete | yes | action.keywords.merge_duplicates.confirm | merge_duplicate_keywords | ActionToolbar |
| words.select_all | words | page | - | words.view | - | - | select_all | ActionToolbar |
| words.select_none | words | page | - | words.view | - | - | select_none | ActionToolbar |
| words.edit_selected | words | selection | one | words.edit | - | - | edit_word | ActionToolbar |
| words.delete_selected | words | bulk | all | words.delete | yes | action.words.delete_selected.confirm | delete_words | ActionToolbar |
| words.open | words | record | - | words.view | - | - | view_details | record + toolbar |
| words.edit | words | record | - | words.edit | - | - | edit_word | record + toolbar |
| words.delete | words | record | - | words.delete | yes | action.words.delete.confirm | delete_word | record + toolbar |
| sources.select_all | sources | page | - | sources.view | - | - | select_all | ActionToolbar |
| sources.select_none | sources | page | - | sources.view | - | - | select_none | ActionToolbar |
| sources.edit_selected | sources | bulk | all | sources.edit | - | - | **none** | ActionToolbar |
| sources.export_selected | sources | bulk | all | export.create | - | - | **none** | ActionToolbar |
| sides.select_all | sides | page | - | sides.view | - | - | select_all | ActionToolbar |
| sides.select_none | sides | page | - | sides.view | - | - | select_none | ActionToolbar |
| sides.edit_selected | sides | bulk | all | sides.edit | - | - | **none** | ActionToolbar |
| sides.export_selected | sides | bulk | all | export.create | - | - | **none** | ActionToolbar |
| jobs.cancel | jobs | record | - | jobs.cancel | - | action.jobs.cancel.confirm | cancel_job | no described screen |

### How each action stands

Four conditions, and they are not the same condition:

* **registered** - the catalog has a row for it;
* **presented** - a screen declaration says that screen offers it (`SCREEN_ACTIONS`);
* **bound** - something in the product names it: an attribute in markup, a macro argument, a string in the page script that drives the control, or an id written in the file a screen declares as its binding source (`BINDING_SOURCES`);
* **built** - the registry names the operation that performs it.

A control a reader can press without the operation existing is exactly the defect this audit was written after, so an action that is presented and not built is a finding, not a style note.

| Condition | Actions |
| --- | --- |
| bound | 5 |
| declared, not built; a page still prepares it | 1 |
| presented by declaration; no control names it | 28 |

The scan found 14 bindings in 6 files: 0 in markup attributes, 9 named as values (a macro argument or a page script), and 5 in the files the screens declare as their binding sources. The attribute count is the one that falls as this layer lands: an action drawn from prepared data needs no id typed into a template.

Every one of those bindings is then resolved against the application's own URL map (`core.experience.bindings.check`), so a control pointing at a route nobody serves is a test failure rather than a 404 a reader finds. That check is what retired the Reprocess link.

### Surfaces the model does not own yet

Each row is a scan from `SURFACES`, so the count and the files come from one statement.

| Surface | Files | Where |
| --- | --- | --- |
| Shared action toolbar | 5 | `templates/Keyword/keywords_list.html`, `templates/Side/sides_list.html`, `templates/Sources/sources_list.html`, `templates/Word/Word_list.html`, `templates/email_words/email_words.html` |
| Hand-written action bar | 1 | `templates/file/files_list.html` |
| Record actions drawn by hand | 3 | `templates/Word/Word_detail.html`, `templates/Word/Word_list.html`, `templates/file/files_list.html` |
| Filter form submitted from a bar | 1 | `templates/email_words/email_words.html` |
| Document viewer controls | 1 | `templates/file/full_content.html` |
| Browser confirm() dialog | 24 | `static/js/pages/analysis-batch-page.js`, `static/js/pages/analyst-categorization-page.js`, `static/js/pages/categories-list-page.js`, `static/js/pages/categories-tree-page.js`, `static/js/pages/category-names-page.js`, `static/js/pages/category-words-page.js`, `static/js/pages/keyword-detail-page.js`, `static/js/pages/keywords-list-page.js`, `static/js/pages/map-viewer-page.js`, `static/js/pages/notifications-page.js`, `static/js/pages/retention-page.js`, `static/js/pages/saved-searches-page.js`, `static/js/pages/scenarios-page.js`, `static/js/pages/schedules-page.js`, `static/js/pages/search-advanced-page.js`, `static/js/pages/search-enhanced-page.js`, `static/js/pages/side-detail-page.js`, `static/js/pages/sides-list-page.js`, `static/js/pages/source-detail-page.js`, `static/js/pages/sources-list-page.js`, `static/js/pages/translation-management-page.js`, `static/js/pages/users-page.js`, `static/js/pages/word-detail-page.js`, `static/js/pages/words-list-page.js` |
| Button state decided by the page | 15 | `static/js/pages/analysis-batch-page.js`, `static/js/pages/categories-list-page.js`, `static/js/pages/email-words-page.js`, `static/js/pages/file-types-page.js`, `static/js/pages/ingestion-studio-page.js`, `static/js/pages/install-wizard-page.js`, `static/js/pages/keywords-list-page.js`, `static/js/pages/lexis-cosmos-page.js`, `static/js/pages/login-page.js`, `static/js/pages/notifications-page.js`, `static/js/pages/search-advanced-page.js`, `static/js/pages/search-enhanced-page.js`, `static/js/pages/signals-page.js`, `static/js/pages/translation-selection.js`, `static/js/pages/users-page.js` |

The component library counts 5 standardised and 2 hand-written action bars. Those are the same bars seen from two directions: the 5 templates rendering the shared toolbar are listed above, and the hand-written ones split into the file list's action bar and the full-content viewer's bar - which this audit counts as a viewer control, because that is what it is, not as an action bar to migrate.

The scripts are scanned as well as the templates, which is why the browser `confirm()` and page-local button state rows are larger here than in the component library: a dialog written in a page's JavaScript is still a dialog nobody owns.

### Candidate surfaces for the next layer

Derived from the table above, not from taste:

* **Record action surface** - the 10 registered record-scope actions, plus the 3 templates that draw record actions themselves.
* **Specialized composites** - a viewer surface (1 template) for the document controls, and a filter surface (1 template) for the bar that submits a filter form.
* **Confirmation dialog** - 7 registered actions require a confirmation, and 24 files still open the browser's own dialog to get one.

### Actions that do not fit the current abstractions

This is the agenda for the next layer: each row is something the model cannot say today, and the code that proves it.

| Action | Where | What the model cannot say | Evidence |
| --- | --- | --- | --- |
| files.reprocess | file_library | The registry can say 'declared, not built' and the surface honours it - the action is hidden, nothing is drawn, and no route is bound - but the model still cannot describe the work: no persistent job type exists for re-extraction, and the pipeline refuses a duplicate hash whose path is already stored. Registering an operation for it means giving it a job and a path policy first, which the action vocabulary has no room for. | `core/experience/action_registry.py`, `Api/blueprints/files.py`, `pipeline/storage_pipeline.py` |
| merge_duplicates | keywords | A page action whose confirmation carries runtime numbers - how many duplicates, what will be merged. `confirmation` is a translation key and nothing else, so the dialog cannot be handed values. | `static/js/pages/keywords-list-page.js` |
| export_selected, edit_selected | sources, sides | Registered with no operation: the controls exist, the registry says so by leaving `execution` empty, and the audit counts them. What the model lacks is a way to say 'shown, not built' that the interface can honour - today the button is simply there, enabled when rows are selected, and does nothing useful. | `static/js/pages/sources-list-page.js`, `static/js/pages/sides-list-page.js` |
| select_all / select_none | keywords, words, sources, sides, file_library | Selection *controls*, registered as page actions because that is the only vocabulary available. They produce the scope the other actions consume; the model has one word for both roles, so the pattern cannot be required of the next screen. | `templates/Sources/sources_list.html`, `templates/Side/sides_list.html` |
| apply_filters | email_words | A page action that is really a filter surface's control (it submits the filter form). Registering it as an action would give one control two owners - the filter definition and the action definition. | `templates/email_words/email_words.html` |
| open / edit / delete (record actions) | file_library, words | Registered with the right scope, but rendered inside the row by hand: no component owns where a record's actions are drawn. That is the Record Action Surface, which does not exist yet. | `templates/Word/Word_list.html`, `templates/file/files_list.html` |
| analyze_selected, export_selected, upload | file_library | Registered, and the model can now say an action is running - but not what it started. There is no job reference on an action, so 'Analysis queued. Job AN-4921' cannot be produced from the definition. | `templates/file/files_list.html`, `static/js/modules/file-operations/file-management.js` |
| view_original, download_original | file_library | The two operations are registered and distinct, and the service behind them is real (inline vs attachment, range requests, path from the database). What the model cannot say is where they are drawn: today they are two hand-written links in the reader, labelled 'Original File' and 'Original' - one of which is a download and does not say so. | `templates/file/full_content.html`, `Api/services/original_file.py` |
| export_data (email_words) | email_words | Loading is implemented by the page (button disabled, label swapped) although the model has a running state, because nothing tells the page which action it belongs to; and the export acts on the filtered set, which is neither page nor selection scope as currently worded. | `static/js/pages/email-words-page.js` |
| viewer controls | file_library | Copy, Download, Print, Wrap, Smaller/Larger, Dark: document controls, not record actions, and deliberately not toolbar buttons. They need a viewer surface, and the audit records them so nobody 'fixes' them into one. | `templates/file/full_content.html` |
| permission | every registered action | Every action now names a permission domain, and nothing enforces one: the vocabulary (core/experience/permissions.py) is a naming scheme for visibility, while the server still authorises by role at the route. Until operations are bound to actions, a permission is a name the inspector can show - not a boundary, and not a reason to hide a control that the server would refuse anyway. | `core/experience/permissions.py` |
| cancel_job, reprocess | jobs, file_library | Registered as record actions with a confirmation, but the screens they live on have no experience contract, and the confirmation is still a browser dialog written in the template or the page's JavaScript. They are the first two actions the shared Confirmation Dialog has to take over. | `templates/Operations/jobs.html`, `templates/file/file_detail.html` |
<!-- END GENERATED: action surfaces -->
