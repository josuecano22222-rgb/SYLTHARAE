from Api.services import translation_management as manager


def test_management_catalog_unions_screen_catalog_and_localization_pack_entries():
    entries = manager.get_source_catalog_entries()
    ids = {entry['id'] for entry in entries}

    # The checked-in POT is the source of screen references, while the PO union
    # adds maintained client strings and the static locale packs cover the
    # remaining JavaScript-only copy.
    assert len(entries) >= 3000
    assert len(ids) == len(entries)
    assert any(
        location['screen_id'] == 'templates/Search/search_advanced.html'
        for entry in entries for location in entry['locations']
    )
    assert any(
        location['path'] == 'static/js/pages/ingestion-studio-page.js'
        for entry in entries for location in entry['locations']
    )
    assert any(entry['msgid'] == 'Choose a server path' for entry in entries)


def test_only_canonical_target_languages_are_editable():
    assert set(manager.supported_translation_languages()) == {'ar', 'he', 'fa'}
    assert manager.validate_translation_locale('ar')
    assert not manager.validate_translation_locale('en')
    assert not manager.validate_translation_locale('hr')


def test_placeholder_signature_preserves_gettext_and_browser_tokens():
    source = 'Hello {name}; %(count)d item(s)'
    changed_order = '%(count)d item(s) for {name}'
    changed_token = 'Hello {user}; %(count)d item(s)'

    assert manager.placeholder_signature(source) == manager.placeholder_signature(changed_order)
    assert manager.placeholder_signature(source) != manager.placeholder_signature(changed_token)


def test_static_javascript_pack_translations_are_the_manager_defaults(monkeypatch):
    monkeypatch.setattr(manager, 'get_translation_overrides', lambda _locale, use_cache=True: {})

    result = manager.translation_management_catalog(
        'ar', query='Choose a server path', per_page=10,
    )

    assert result['total'] == 1
    assert result['items'][0]['base_translation'] == 'اختر مسارًا على الخادم'
    assert result['items'][0]['screen_ids'] == ['static/js/pages/ingestion-studio-page.js']


def test_catalog_filters_and_marks_custom_overrides(monkeypatch):
    entries = manager.get_source_catalog_entries()
    entry = next(row for row in entries if row['msgid'] == 'Translation Management')
    monkeypatch.setattr(manager, 'get_translation_overrides', lambda _locale, use_cache=True: {
        entry['id']: {
            'msgid': entry['msgid'],
            'translation': 'SYLTHARAE translation test',
        }
    })

    result = manager.translation_management_catalog(
        'ar', query='Translation Management', status='overridden', per_page=10,
    )

    assert result['total'] == 1
    assert result['items'][0]['id'] == entry['id']
    assert result['items'][0]['translation'] == 'SYLTHARAE translation test'
    assert result['items'][0]['base_translation'] == 'إدارة الترجمات'
    assert result['items'][0]['overridden'] is True
    assert result['stats']['overridden'] == 1


def test_runtime_override_updates_the_current_babel_catalog(monkeypatch):
    entry = manager.source_entry_for_id(
        next(row['id'] for row in manager.get_source_catalog_entries()
             if row['msgid'] == 'Translation Management')
    )
    override = {
        entry['id']: {'msgid': entry['msgid'], 'translation': 'Localized runtime value'}
    }
    monkeypatch.setattr(manager, 'get_translation_overrides', lambda _locale: override)

    class FakeTranslations:
        _catalog = {}

    translations = FakeTranslations()
    monkeypatch.setattr('flask_babel.get_translations', lambda: translations)

    manager.apply_runtime_translation_overrides('ar')

    assert translations._catalog['Translation Management'] == 'Localized runtime value'
