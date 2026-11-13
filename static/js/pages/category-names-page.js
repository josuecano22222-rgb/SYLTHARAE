/**
 * Curate a category's names in every supported language.
 *
 * The page is server-rendered; this script only edits. Every request goes to
 * /api/term-names, which validates the language/script pair before PostgreSQL
 * does, so a wrong-script name is refused with a sentence rather than a
 * constraint name. After a write the page reloads: there is no optimistic
 * update, because the list is the evidence.
 */
(function () {
    'use strict';

    function categoryId() {
        var node = document.querySelector('[data-category-id]');
        if (node) return node.getAttribute('data-category-id');
        var match = window.location.pathname.match(/\/categories\/(\d+)\/names/);
        return match ? match[1] : null;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    async function postJson(url, body, method) {
        var response = await fetch(url, {
            method: method || 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken()
            },
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        var data = null;
        try { data = await response.json(); } catch (e) { data = null; }
        if (!response.ok) {
            var message = (data && (data.error || data.message)) || response.statusText;
            throw new Error(message);
        }
        return data;
    }

    function showError(prefix, error) {
        window.alert(prefix ? prefix + ': ' + error.message : error.message);
    }

    window.openAddNameModal = function () {
        document.getElementById('nameId').value = '';
        document.getElementById('nameText').value = '';
        var modal = new bootstrap.Modal(document.getElementById('nameModal'));
        modal.show();
    };

    window.editName = function (nameId) {
        var row = document.querySelector('tr[data-name-id="' + nameId + '"]');
        if (!row) return;
        document.getElementById('nameId').value = nameId;
        document.getElementById('nameText').value = row.children[1].textContent.trim();
        var language = row.children[0].textContent.trim();
        var select = document.getElementById('nameLanguage');
        if (select) select.value = language;
        new bootstrap.Modal(document.getElementById('nameModal')).show();
    };

    window.saveName = async function () {
        var nameId = document.getElementById('nameId').value;
        var payload = {
            category_id: Number(categoryId()),
            language: document.getElementById('nameLanguage').value,
            name: document.getElementById('nameText').value
        };
        try {
            if (nameId) {
                await postJson('/api/term-names/' + nameId,
                               {name: payload.name, language: payload.language}, 'PUT');
            } else {
                await postJson('/api/term-names', payload);
            }
            window.location.reload();
        } catch (error) {
            showError('', error);
        }
    };

    window.deleteName = async function (nameId) {
        // The confirmation text is translated on the server and read from the
        // button: a literal "Delete" here would be the one English word left on
        // an Arabic page.
        var button = document.querySelector(
            'tr[data-name-id="' + nameId + '"] [data-confirm]');
        var question = button ? button.getAttribute('data-confirm') : '';
        if (!window.confirm(question)) return;
        try {
            await postJson('/api/term-names/' + nameId, undefined, 'DELETE');
            window.location.reload();
        } catch (error) {
            showError('', error);
        }
    };

    async function uploadCsv(apply) {
        var input = document.getElementById('nameCsvFile');
        var report = document.getElementById('nameCsvReport');
        if (!input || !input.files || !input.files.length) return;
        // The endpoint reads the CSV as text; multipart boundaries are not
        // part of the file. Sending ``text/csv`` directly keeps the bytes the
        // user chose and avoids a parser having to strip a form envelope.
        var text = await input.files[0].text();
        var response = await fetch('/api/term-names/import' + (apply ? '?apply=1' : ''),
                                   {method: 'POST', body: text,
                                    headers: {'Content-Type': 'text/csv',
                                              'X-CSRFToken': csrfToken()}});
        var data = await response.json().catch(function () { return {}; });
        report.classList.remove('d-none');
        report.textContent = JSON.stringify(data, null, 2);
        if (apply && response.ok) {
            window.setTimeout(function () { window.location.reload(); }, 1200);
        }
    }

    window.previewNameCsv = function () { uploadCsv(false); };
    window.applyNameCsv = function () { uploadCsv(true); };

    document.addEventListener('DOMContentLoaded', function () {
        var page = document.querySelector('.page-header');
        if (page && page.dataset && !page.dataset.ready) page.dataset.ready = '1';
    });
})();
