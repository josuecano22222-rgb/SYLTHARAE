from __future__ import annotations

from flask import Flask

from Api.blueprints import files as files_module


def _files_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(files_module.files_bp)
    return app.test_client()


def test_filename_export_refuses_a_partially_missing_selection(monkeypatch):
    monkeypatch.setattr(files_module, "execute_query", lambda *_args, **_kwargs: [(7, "one.txt", "txt")])
    response = _files_client().post(
        "/api/files/names/export",
        json={"scope": "selected", "file_ids": [7, 8], "format": "csv"},
    )
    assert response.status_code == 404
    assert response.get_json()["missing_file_ids"] == [8]


def test_first_page_export_refuses_a_partially_missing_selection(monkeypatch):
    monkeypatch.setattr(files_module, "execute_query", lambda *_args, **_kwargs: [(7, "one.txt", "/private/one.txt")])
    response = _files_client().post(
        "/api/files/first-pages/export",
        json={"file_ids": [7, 8], "format": "txt"},
    )
    assert response.status_code == 404
    assert response.get_json()["missing_file_ids"] == [8]


def test_contact_export_refuses_a_partially_missing_selection(monkeypatch):
    monkeypatch.setattr(files_module, "execute_query", lambda *_args, **_kwargs: [(7, "one.txt")])
    response = _files_client().post(
        "/api/files/extract-contacts/export",
        json={"file_ids": [7, 8], "format": "csv"},
    )
    assert response.status_code == 404
    assert response.get_json()["missing_file_ids"] == [8]


def test_empty_contact_export_is_explicit_and_reports_zero_matches(monkeypatch):
    monkeypatch.setattr(files_module, "execute_query", lambda *_args, **_kwargs: [(7, "one.txt")])
    monkeypatch.setattr(files_module, "load_text_content", lambda _file_id: "No contacts in this document.")
    response = _files_client().post(
        "/api/files/extract-contacts/export",
        json={"file_ids": [7], "format": "csv", "filename": "contacts"},
    )
    assert response.status_code == 200
    assert response.headers["X-Export-Entities"] == "0"
    assert response.headers["X-Export-Documents"] == "1"
    assert response.headers["X-Export-Empty"] == "true"
    assert "file_id,file_name,kind,value,occurrences,location,first_line,context" in response.get_data(as_text=True)


def test_contact_export_refuses_to_create_a_partial_file_when_text_is_unavailable(monkeypatch):
    monkeypatch.setattr(files_module, "execute_query", lambda *_args, **_kwargs: [(7, "one.txt")])
    monkeypatch.setattr(files_module, "load_text_content", lambda _file_id: None)
    response = _files_client().post(
        "/api/files/extract-contacts/export",
        json={"file_ids": [7], "format": "csv"},
    )
    assert response.status_code == 422
    assert response.get_json()["unavailable_file_ids"] == [7]


def test_in_document_search_finds_all_positive_query_terms_with_exact_options(monkeypatch):
    monkeypatch.setattr(
        files_module,
        "load_text_content",
        lambda _file_id: "Alpha and alphabet.\nbeta stands alone.\nexcluded term.",
    )
    response = _files_client().get(
        "/file/11/search",
        query_string={
            "q": "Alpha OR beta NOT excluded",
            "case_sensitive": "true",
            "whole_word": "true",
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert [item["text"] for item in payload["matches"]] == ["Alpha", "beta"]
    assert payload["search_options"] == {"case_sensitive": True, "whole_word": True}
