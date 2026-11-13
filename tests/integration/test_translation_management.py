def _csrf_token(client):
    response = client.get('/api/csrf-token')
    assert response.status_code == 200
    return response.get_json()['csrf_token']


def test_translation_manager_is_admin_only_and_edits_runtime_catalog(admin_client, client_factory):
    analyst = client_factory('analyst')
    assert analyst.get('/translations/manage').status_code == 403
    assert analyst.get('/api/translations/manage/catalog?locale=ar').status_code == 403
    analyst_headers = {'X-CSRFToken': _csrf_token(analyst)}
    assert analyst.put(
        '/api/translations/manage/catalog', json={}, headers=analyst_headers,
    ).status_code == 403

    page = admin_client.get('/translations/manage')
    assert page.status_code == 200
    page_html = page.get_data(as_text=True)
    assert 'data-translation-manager' in page_html
    assert 'data-interface="translation_manager"' in page_html

    token = _csrf_token(admin_client)
    headers = {'X-CSRFToken': token}
    catalog_response = admin_client.get(
        '/api/translations/manage/catalog?locale=ar&q=Translation%20Management&per_page=10'
    )
    assert catalog_response.status_code == 200, catalog_response.get_data(as_text=True)
    entry = next(
        row for row in catalog_response.get_json()['items']
        if row['msgid'] == 'Translation Management'
    )
    baseline = entry['base_translation']
    entry_id = entry['id']

    # Ensure the test always leaves the checked-in PO value effective.
    reset_payload = {
        'locale': 'ar',
        'translations': [{'id': entry_id, 'reset': True}],
    }
    admin_client.put(
        '/api/translations/manage/catalog', json=reset_payload, headers=headers,
    )

    invalid_placeholder = admin_client.put(
        '/api/translations/manage/catalog',
        json={'locale': 'ar', 'translations': [{'id': entry_id, 'translation': '{missing}'}]},
        headers=headers,
    )
    assert invalid_placeholder.status_code == 400

    changed_value = 'SYLTHARAE translation runtime check'
    save_response = admin_client.put(
        '/api/translations/manage/catalog',
        json={'locale': 'ar', 'translations': [{'id': entry_id, 'translation': changed_value}]},
        headers=headers,
    )
    assert save_response.status_code == 200, save_response.get_data(as_text=True)

    with admin_client.session_transaction() as session:
        session['language'] = 'ar'
    rendered = admin_client.get('/translations/manage')
    assert rendered.status_code == 200
    assert changed_value in rendered.get_data(as_text=True)

    client_catalog = admin_client.get('/api/i18n/catalog?locale=ar').get_json()
    assert client_catalog['translations']['Translation Management'] == changed_value

    try:
        reset_response = admin_client.put(
            '/api/translations/manage/catalog', json=reset_payload, headers=headers,
        )
        assert reset_response.status_code == 200, reset_response.get_data(as_text=True)
        restored = admin_client.get('/api/i18n/catalog?locale=ar').get_json()
        assert restored['translations']['Translation Management'] == baseline
    finally:
        # Be defensive if an assertion above fails partway through the test.
        admin_client.put(
            '/api/translations/manage/catalog', json=reset_payload, headers=headers,
        )


def test_the_client_catalog_survives_plural_entries(admin_client):
    """NARR-01 added the first msgid_plural entries (report narrative, used on
    the server through ngettext). The client catalog used them as JSON keys
    (tuples), jsonify failed, and the handler answered 200 with an *empty*
    catalog: every JavaScript-rendered string lost its translation. Plural
    entries are left out; every flat string is still served."""
    from babel.messages.pofile import read_po

    from core.analytics.kinds import KEYNESS_TEMPLATES_V2

    plural = KEYNESS_TEMPLATES_V2.plurals()[0]
    for locale in ('ar', 'he', 'fa', 'en'):
        path = f'translations/{locale}/LC_MESSAGES/messages.po'
        with open(path, 'rb') as fh:
            catalog = read_po(fh, locale=locale)
        assert catalog.get(plural.singular).pluralizable, 'the catalog has plural entries'
        body = admin_client.get(f'/api/i18n/catalog?locale={locale}').get_json()
        assert body['success'] is True, body
        assert body['locale'] == locale
        if locale == 'en':
            continue
        flat = sum(1 for m in catalog if isinstance(m.id, str) and m.id
                   and isinstance(m.string, str) and m.string)
        assert body['count'] >= flat > 1000, (locale, body['count'], flat)
        assert plural.singular not in body['translations']
        assert body['translations']['Translation Management'], locale


def test_resetting_an_override_changes_the_rendered_page_without_a_restart(admin_client):
    """Deleting an edited translation has to be visible on the next request.

    The overlay writes saved edits into the Babel catalog it is handed. That
    catalog is cached for the process, so the first version of this bug left
    the edit in place after the row was deleted: the editor said "reset", the
    database agreed, and every page kept rendering the old text until the
    service was restarted. Found from a module-order failure - a later module
    asserted the checked-in translation and saw an earlier module's edit.
    """
    token = _csrf_token(admin_client)
    headers = {'X-CSRFToken': token}
    catalog = admin_client.get(
        '/api/translations/manage/catalog?locale=ar&q=Translation%20Management&per_page=10'
    ).get_json()
    entry = next(row for row in catalog['items'] if row['msgid'] == 'Translation Management')
    base = entry['base_translation']
    assert base and base != 'Translation Management'

    marker = 'ترجمة مؤقتة للاختبار'
    reset_payload = {'locale': 'ar', 'translations': [{'id': entry['id'], 'reset': True}]}
    with admin_client.session_transaction() as session:
        session['language'] = 'ar'
    try:
        saved = admin_client.put(
            '/api/translations/manage/catalog',
            json={'locale': 'ar',
                  'translations': [{'id': entry['id'], 'translation': marker}]},
            headers=headers)
        assert saved.status_code == 200, saved.get_data(as_text=True)
        assert marker in admin_client.get('/translations/manage').get_data(as_text=True)

        reset = admin_client.put('/api/translations/manage/catalog',
                                 json=reset_payload, headers=headers)
        assert reset.status_code == 200, reset.get_data(as_text=True)
        rendered = admin_client.get('/translations/manage').get_data(as_text=True)
        assert marker not in rendered, (
            'the deleted override is still rendered: the catalog cache kept it')
        assert base in rendered
    finally:
        admin_client.put('/api/translations/manage/catalog', json=reset_payload,
                         headers=headers)
        with admin_client.session_transaction() as session:
            session['language'] = 'en'
