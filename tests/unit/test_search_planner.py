from core.search.enums import SearchClass, SearchComplexity, SearchEngineRoute
from core.search.planner import plan_search


def test_planner_routes_indexed_terms_to_inverted_index():
    plan = plan_search(query="financial report", has_metadata_filters=False)
    assert plan.search_class == SearchClass.INDEXED_TERM
    assert plan.primary_engine == SearchEngineRoute.PG_INVERTED
    assert plan.complexity in {SearchComplexity.FAST, SearchComplexity.NORMAL}


def test_planner_literal_path_for_boolean():
    plan = plan_search(
        query='"alpha" AND beta',
        has_metadata_filters=False,
        has_boolean_ops=True,
        has_quotes=True,
    )
    assert plan.primary_engine == SearchEngineRoute.PG_LITERAL
    assert plan.complexity == SearchComplexity.EXPENSIVE


def test_planner_metadata_only():
    plan = plan_search(query="", has_metadata_filters=True)
    assert plan.search_class == SearchClass.METADATA
    assert plan.defer_snippets is True
