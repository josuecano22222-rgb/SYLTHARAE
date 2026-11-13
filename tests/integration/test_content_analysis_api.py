"""The /api/analysis routes answer from the engine that exists (ANALYSIS-01).

They used to call ContentAnalysisEngine.analyze_file/analyze_batch, which the
engine never had: every request was a 500. Only analyze_content is patched
here, on the real class, so a route calling a method that does not exist
still fails these tests.
"""
import pytest

from Api.blueprints import content_analysis
from database.analyzers.analysis_engine import ContentAnalysisEngine

ANALYSIS = {"path_id": 7, "word_count": 5, "unique_words": 4,
            "categories": {"finance": 2}, "content_preview": "a b c"}


@pytest.fixture
def engine_returns(monkeypatch):
    def install(result):
        monkeypatch.setattr(ContentAnalysisEngine, "analyze_content",
                            lambda self, path_id: dict(result, path_id=path_id))
        monkeypatch.setattr(content_analysis, "_file_exists", lambda file_id: file_id != 404)
    return install


def test_file_analysis(admin_client, engine_returns):
    engine_returns(ANALYSIS)
    r = admin_client.get("/api/analysis/file/7")
    assert r.status_code == 200
    assert r.json == {"success": True, "data": ANALYSIS}


def test_statistics(admin_client, engine_returns):
    engine_returns(ANALYSIS)
    r = admin_client.get("/api/analysis/statistics/7")
    assert r.status_code == 200
    assert r.json["data"] == {"file_id": 7, "word_count": 5, "unique_words": 4,
                              "category_count": 1, "categories": {"finance": 2}}


def test_batch(admin_client, engine_returns):
    engine_returns(ANALYSIS)
    r = admin_client.post("/api/analysis/batch", json={"file_ids": [7, 404, 7, 8]})
    assert r.status_code == 200
    data = r.json["data"]
    assert [f["path_id"] for f in data["files"]] == [7, 8]
    assert data["not_found"] == [404]
    assert data["total_words"] == 10


@pytest.mark.parametrize("body", [{}, {"file_ids": []}, {"file_ids": "1"},
                                  {"file_ids": [1, "2"]}, {"file_ids": [True]},
                                  {"file_ids": list(range(101))}])
def test_batch_rejects_bad_requests(admin_client, engine_returns, body):
    engine_returns(ANALYSIS)
    assert admin_client.post("/api/analysis/batch", json=body).status_code == 400


def test_unknown_file_is_404_from_the_real_lookup(admin_client):
    for route in ("file", "statistics"):
        r = admin_client.get(f"/api/analysis/{route}/987654321")
        assert r.status_code == 404, route


def test_engine_errors_stay_in_the_log(admin_client, engine_returns):
    engine_returns({"error": "connection to server at 10.0.0.5 failed: password=hunter2"})
    for route in ("file", "statistics"):
        r = admin_client.get(f"/api/analysis/{route}/7")
        assert r.status_code == 500
        body = r.get_data(as_text=True)
        assert "hunter2" not in body and "10.0.0.5" not in body
        assert r.json["correlation_id"]


@pytest.mark.parametrize("route", ["sentiment", "topics", "entities"])
def test_unimplemented_analyses_say_so(admin_client, route):
    r = admin_client.get(f"/api/analysis/{route}/7")
    assert r.status_code == 501
    assert r.json["code"] == "not_implemented"


def test_analysis_requires_sign_in(client):
    assert client.get("/api/analysis/file/7").status_code == 401
