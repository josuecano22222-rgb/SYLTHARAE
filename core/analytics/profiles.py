"""Profile analyses for catalog list reports (horizon, places, keywords,
categories).

Each kind reads one capped listing from a completed run and speaks in the
five narrative voices. Counts are taken from the rows the run stored - never
from a second database query - so a printed artifact can be read as evidence
of what those rows showed at that snapshot.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Tuple

from .kinds import (
    MEASURED, NOT_MEASURABLE, AnalysisError, InputRole, Kind, KINDS,
)
from .narrative import Plural, TemplateSet

# ---------------------------------------------------------------------------
# horizon_profile
# ---------------------------------------------------------------------------

HORIZON_TEMPLATES = TemplateSet("horizon_profile", 1, {
    "measure.method": (
        "The horizon groups detected future-oriented dates and relative "
        "references in the matched documents by how soon they fall relative "
        "to the declared reference date: overdue, next 7 days, next 30 days, "
        "next 90 days, and later."),
    "measure.sizes": Plural(
        "Listed: %(signals)s temporal signal from the matched documents.",
        "Listed: %(signals)s temporal signals from the matched documents.",
        "signals"),
    "measure.truncated": (
        "The listing is a capped prefix: more matching signals existed than "
        "were stored in this run."),
    "measure.complete": (
        "The listing includes every matching horizon signal in this "
        "snapshot (the row limit was not reached)."),
    "measure.not_measurable": (
        "The horizon measures future-oriented dates in the matched "
        "documents; none were listed for this run."),
    "finding.top_bucket": Plural(
        "The densest bucket is %(bucket)s, with %(count)s signal "
        "(%(share)s%% of those listed).",
        "The densest bucket is %(bucket)s, with %(count)s signals "
        "(%(share)s%% of those listed).",
        "count"),
    "finding.distribution": (
        "Bucket counts: overdue %(overdue)s; next 7 days %(week)s; "
        "next 30 days %(month)s; next 90 days %(quarter)s; later %(later)s."),
    "finding.soonest": (
        "The soonest listed event date is %(soonest)s; the latest is %(latest)s."),
    "finding.none": (
        "No temporal signal falls on the horizon for this selection and "
        "reference date."),
    "confidence.counts": (
        "Bucket totals are exact counts of the signals listed in this run. "
        "They describe the stored listing, not an estimate of the whole "
        "collection."),
    "confidence.not_measurable": "No horizon count was performed.",
    "consequence.read": (
        "These buckets show when the matched documents speak of forthcoming "
        "events, not why those dates were written. Read the evidence "
        "sentences and open the source files before drawing operational "
        "conclusions."),
    "consequence.not_measurable": "No conclusion can be drawn from this section.",
    "caveat.scope": (
        "Undated references and purely past mentions are outside the "
        "horizon. Confidence and method may be unknown for older stored "
        "signals. Next step: open the files that carry the densest bucket."),
    "caveat.not_measurable": (
        "Next step: widen the criteria, choose another reference date, or "
        "check that the matching documents were analysed by the temporal "
        "detector."),
}, record_format=2)


def _horizon_profile(inputs: Mapping[str, Dict[str, Any]],
                     params: Mapping[str, Any],
                     narrate: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    listing = inputs["signals"]
    rows = listing["rows"]
    if not rows:
        return {"state": NOT_MEASURABLE, "reason": "none", "measures": {"signals": 0},
                "rows": [], "narrative": narrate(reason="none")}
    buckets = Counter(str(r.get("bucket") or "") for r in rows)
    for key in ("overdue", "week", "month", "quarter", "later"):
        buckets.setdefault(key, 0)
    total = len(rows)
    top_bucket, top_count = max(
        ((k, buckets[k]) for k in ("overdue", "week", "month", "quarter", "later")),
        key=lambda item: (item[1], -["overdue", "week", "month", "quarter", "later"].index(item[0])))
    dates = [str(r["event_date"]) for r in rows if r.get("event_date") is not None]
    soonest = min(dates) if dates else None
    latest = max(dates) if dates else None
    share = round(top_count / total * 100, 2) if total else 0
    m = {
        "signals": total,
        "overdue": buckets["overdue"], "week": buckets["week"],
        "month": buckets["month"], "quarter": buckets["quarter"],
        "later": buckets["later"],
        "top_bucket": top_bucket, "top_count": top_count, "share": share,
        "truncated": bool(listing["truncated"]),
        "soonest": soonest, "latest": latest,
    }
    measure: List[Tuple[str, Mapping[str, Any]]] = [
        ("measure.method", {}),
        ("measure.sizes", {"signals": total}),
        ("measure.truncated", {}) if listing["truncated"] else ("measure.complete", {}),
    ]
    finding = [
        ("finding.top_bucket", {"bucket": top_bucket, "count": top_count, "share": share}),
        ("finding.distribution", {k: m[k] for k in
                                  ("overdue", "week", "month", "quarter", "later")}),
    ]
    if soonest and latest:
        finding.append(("finding.soonest", {"soonest": soonest, "latest": latest}))
    return {"state": MEASURED, "reason": None, "measures": m, "rows": [],
            "narrative": narrate(measure=measure, finding=finding)}


def _narrate_horizon(reason=None, measure=None, finding=None):
    T = HORIZON_TEMPLATES
    if reason:
        return T.compose([
            ("measure.not_measurable", {}), ("finding.none", {}),
            ("confidence.not_measurable", {}), ("consequence.not_measurable", {}),
            ("caveat.not_measurable", {}),
        ])
    return T.compose([measure, finding, ("confidence.counts", {}),
                      ("consequence.read", {}), ("caveat.scope", {})])


HORIZON_PROFILE = Kind(
    name="horizon_profile", version=1,
    inputs={"signals": InputRole(("capped",),
                                 ("bucket", "event_date", "signal_id"))},
    thresholds=(),
    templates=HORIZON_TEMPLATES,
    compute=lambda inputs, params: _horizon_profile(inputs, params, _narrate_horizon),
)

# ---------------------------------------------------------------------------
# place_profile
# ---------------------------------------------------------------------------

PLACE_TEMPLATES = TemplateSet("place_profile", 1, {
    "measure.method": (
        "Place attribution counts gazetteer places detected in the matched "
        "documents. Identified mentions are resolved to one place; ambiguous "
        "mentions keep every candidate and are never merged into the "
        "identified totals."),
    "measure.sizes": Plural(
        "Listed: %(places)s place with detections in the matched documents.",
        "Listed: %(places)s places with detections in the matched documents.",
        "places"),
    "measure.totals": (
        "Identified mentions: %(identified)s across %(identified_contents)s "
        "contents. Ambiguous candidate mentions: %(ambiguous)s across "
        "%(ambiguous_contents)s contents."),
    "measure.truncated": (
        "The listing is a capped prefix: more matching places existed than "
        "were stored in this run."),
    "measure.complete": (
        "The listing includes every matching place in this snapshot (the "
        "row limit was not reached)."),
    "measure.not_measurable": (
        "Place attribution measures gazetteer detections in the matched "
        "documents; none were listed for this run."),
    "finding.top": (
        "The place with the most identified mentions is \"%(label)s\" "
        "(%(feature)s): %(identified)s identified mentions in "
        "%(identified_contents)s contents; ambiguous candidate mentions: "
        "%(ambiguous)s."),
    "finding.ambiguity": (
        "Ambiguous candidate mentions are %(ambiguous_share)s%% of all "
        "listed place mentions (identified plus ambiguous). Ambiguity is "
        "evidence of unresolved identity, not a second count of the same "
        "fact."),
    "finding.none": (
        "No gazetteer place was detected in the matched documents."),
    "confidence.counts": (
        "Identified and ambiguous totals are exact sums over the places "
        "listed in this run. Mentions without recorded confidence are kept "
        "separate and never treated as zero."),
    "confidence.not_measurable": "No place count was performed.",
    "consequence.read": (
        "These figures show where the matched documents name places and "
        "where the detector could not choose among candidates. They do not "
        "assert that a place is relevant to a case: read the evidence and "
        "the ambiguous candidate set before deciding."),
    "consequence.not_measurable": "No conclusion can be drawn from this section.",
    "caveat.scope": (
        "Unresolved mentions that name no place are outside per-place "
        "attribution. Retired gazetteer entries remain listed with their "
        "flag. Next step: open the representative files for the densest "
        "places."),
    "caveat.not_measurable": (
        "Next step: widen the criteria, or check that the matching "
        "documents were analysed by the places detector."),
}, record_format=2)


def _place_profile(inputs: Mapping[str, Dict[str, Any]],
                   params: Mapping[str, Any],
                   narrate: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    listing = inputs["places"]
    rows = listing["rows"]
    if not rows:
        return {"state": NOT_MEASURABLE, "reason": "none", "measures": {"places": 0},
                "rows": [], "narrative": narrate(reason="none")}
    identified = sum(int(r["identified_occurrences"]) for r in rows)
    ambiguous = sum(int(r["ambiguous_occurrences"]) for r in rows)
    identified_contents = sum(int(r["identified_contents"]) for r in rows)
    ambiguous_contents = sum(int(r["ambiguous_contents"]) for r in rows)
    top = max(rows, key=lambda r: (int(r["identified_occurrences"]),
                                   int(r["identified_contents"]),
                                   str(r["place_label"])))
    total_mentions = identified + ambiguous
    amb_share = round(ambiguous / total_mentions * 100, 2) if total_mentions else 0.0
    m = {
        "places": len(rows),
        "identified": identified, "ambiguous": ambiguous,
        "identified_contents": identified_contents,
        "ambiguous_contents": ambiguous_contents,
        "ambiguous_share": amb_share,
        "truncated": bool(listing["truncated"]),
        "top_label": top["place_label"],
    }
    measure = [
        ("measure.method", {}),
        ("measure.sizes", {"places": len(rows)}),
        ("measure.totals", {
            "identified": identified, "identified_contents": identified_contents,
            "ambiguous": ambiguous, "ambiguous_contents": ambiguous_contents}),
        ("measure.truncated", {}) if listing["truncated"] else ("measure.complete", {}),
    ]
    finding = [
        ("finding.top", {
            "label": top["place_label"], "feature": top["feature_type"],
            "identified": int(top["identified_occurrences"]),
            "identified_contents": int(top["identified_contents"]),
            "ambiguous": int(top["ambiguous_occurrences"])}),
        ("finding.ambiguity", {"ambiguous_share": amb_share}),
    ]
    return {"state": MEASURED, "reason": None, "measures": m, "rows": [],
            "narrative": narrate(measure=measure, finding=finding)}


def _narrate_place(reason=None, measure=None, finding=None):
    T = PLACE_TEMPLATES
    if reason:
        return T.compose([
            ("measure.not_measurable", {}), ("finding.none", {}),
            ("confidence.not_measurable", {}), ("consequence.not_measurable", {}),
            ("caveat.not_measurable", {}),
        ])
    return T.compose([measure, finding, ("confidence.counts", {}),
                      ("consequence.read", {}), ("caveat.scope", {})])


PLACE_PROFILE = Kind(
    name="place_profile", version=1,
    inputs={"places": InputRole(
        ("capped",),
        ("place_label", "feature_type", "identified_contents", "identified_occurrences",
         "ambiguous_contents", "ambiguous_occurrences"))},
    thresholds=(),
    templates=PLACE_TEMPLATES,
    compute=lambda inputs, params: _place_profile(inputs, params, _narrate_place),
)

# ---------------------------------------------------------------------------
# keyword_profile
# ---------------------------------------------------------------------------

KEYWORD_TEMPLATES = TemplateSet("keyword_profile", 1, {
    "measure.method": (
        "Keyword reach counts how many of the matched contents contain each "
        "stored keyword, and how often those keywords occur in them. Keywords "
        "come from the whole collection; zeros are listed when a keyword is "
        "absent from the matched set."),
    "measure.sizes": (
        "Matched contents: %(corpus)s. Keywords listed: %(listed)s; of those, "
        "%(present)s appear in at least one matched content."),
    "measure.truncated": (
        "The listing is a capped prefix: more keywords existed than were "
        "stored in this run."),
    "measure.complete": (
        "The listing includes every keyword in this snapshot (the row limit "
        "was not reached)."),
    "measure.not_measurable": (
        "Keyword reach is measured inside the matched contents; the run "
        "stored no keyword rows."),
    "finding.top": (
        "The widest keyword is \"%(label)s\" (category \"%(category)s\"): "
        "present in %(contents)s of %(corpus)s matched contents "
        "(%(share)s%%); stored occurrences: %(occurrences)s."),
    "finding.none_present": (
        "No listed keyword reaches the matched contents: every listed reach "
        "is zero, which is a measurement, not an unknown."),
    "finding.empty": "No keyword rows were stored for this run.",
    "confidence.counts": (
        "Reaches are exact counts of distinct matched contents for each "
        "listed keyword. Shares use the same matched-set size carried on "
        "every row."),
    "confidence.not_measurable": "No keyword reach was measured.",
    "consequence.read": (
        "Reach shows which stored keywords the matched set uses most, not "
        "why those words appear. Next step: open the contents that carry "
        "the widest keywords."),
    "consequence.not_measurable": "No conclusion can be drawn from this section.",
    "caveat.scope": (
        "A keyword belongs to one category; a content can contain many "
        "keywords. Word records without a stored count are excluded from "
        "occurrence totals and counted separately."),
    "caveat.not_measurable": (
        "Next step: widen the criteria, or check that keywords and word "
        "counts were processed for the matching documents."),
}, record_format=2)


def _keyword_profile(inputs: Mapping[str, Dict[str, Any]],
                     params: Mapping[str, Any],
                     narrate: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    listing = inputs["keywords"]
    rows = listing["rows"]
    if not rows:
        return {"state": NOT_MEASURABLE, "reason": "empty", "measures": {"listed": 0},
                "rows": [], "narrative": narrate(reason="empty")}
    corpus_values = {int(r["corpus_contents"]) for r in rows}
    if len(corpus_values) != 1:
        raise AnalysisError(
            "keyword_profile: corpus_contents disagrees across keyword rows")
    corpus = corpus_values.pop()
    present_rows = [r for r in rows if int(r["contents"]) > 0]
    m = {
        "listed": len(rows), "present": len(present_rows), "corpus": corpus,
        "truncated": bool(listing["truncated"]),
    }
    measure = [
        ("measure.method", {}),
        ("measure.sizes", {"corpus": corpus, "listed": len(rows),
                           "present": len(present_rows)}),
        ("measure.truncated", {}) if listing["truncated"] else ("measure.complete", {}),
    ]
    if present_rows:
        top = present_rows[0]  # dataset is ordered widest first
        share = round(int(top["contents"]) / corpus * 100, 2) if corpus else 0.0
        finding = [("finding.top", {
            "label": top["label"], "category": top["category_name"],
            "contents": int(top["contents"]), "corpus": corpus, "share": share,
            "occurrences": int(top["occurrences"])})]
        m.update({"top_label": top["label"], "top_share": share})
    else:
        finding = [("finding.none_present", {})]
    return {"state": MEASURED, "reason": None, "measures": m, "rows": [],
            "narrative": narrate(measure=measure, finding=finding)}


def _narrate_keyword(reason=None, measure=None, finding=None):
    T = KEYWORD_TEMPLATES
    if reason:
        finding_key = "finding.empty" if reason == "empty" else "finding.none_present"
        return T.compose([
            ("measure.not_measurable", {}), (finding_key, {}),
            ("confidence.not_measurable", {}), ("consequence.not_measurable", {}),
            ("caveat.not_measurable", {}),
        ])
    return T.compose([measure, finding, ("confidence.counts", {}),
                      ("consequence.read", {}), ("caveat.scope", {})])


KEYWORD_PROFILE = Kind(
    name="keyword_profile", version=1,
    inputs={"keywords": InputRole(
        ("capped",),
        ("label", "category_name", "contents", "occurrences", "corpus_contents"))},
    thresholds=(),
    templates=KEYWORD_TEMPLATES,
    compute=lambda inputs, params: _keyword_profile(inputs, params, _narrate_keyword),
)

# ---------------------------------------------------------------------------
# category_profile
# ---------------------------------------------------------------------------

CATEGORY_TEMPLATES = TemplateSet("category_profile", 1, {
    "measure.method": (
        "Category reach counts how many of the matched contents contain at "
        "least one word of each category, how often those words occur, and "
        "each category's share of the matched set."),
    "measure.sizes": (
        "Matched contents: %(corpus)s. Categories listed: %(listed)s; of "
        "those, %(present)s reach at least one matched content."),
    "measure.truncated": (
        "The listing is a capped prefix: more categories existed than were "
        "stored in this run."),
    "measure.complete": (
        "The listing includes every category in this snapshot (the row "
        "limit was not reached)."),
    "measure.not_measurable": (
        "Category reach is measured inside the matched contents; the run "
        "stored no category rows."),
    "finding.top": (
        "The widest category is \"%(name)s\": %(contents)s of %(corpus)s "
        "matched contents (%(share)s%%) contain at least one of its words; "
        "stored occurrences: %(occurrences)s; member words: %(members)s."),
    "finding.none_present": (
        "No listed category reaches the matched contents: every listed "
        "share is zero, which is a measurement, not an unknown."),
    "finding.empty": "No category rows were stored for this run.",
    "confidence.counts": (
        "Reaches are exact counts of distinct matched contents. Shares are "
        "those counts against the same matched set. Categories that share "
        "words are not a partition: shares can overlap and can sum to more "
        "than 100%%."),
    "confidence.not_measurable": "No category reach was measured.",
    "consequence.read": (
        "Category reach shows which analytical themes the matched set "
        "touches most often. It does not explain why. Next step: open the "
        "contents counted under the widest categories."),
    "consequence.not_measurable": "No conclusion can be drawn from this section.",
    "caveat.overlap": (
        "Because categories can share words, one content can belong to "
        "several categories at once. Treat shares as overlapping coverage, "
        "never as exclusive slices."),
    "caveat.not_measurable": (
        "Next step: widen the criteria, or check that category membership "
        "and word counts were processed for the matching documents."),
}, record_format=2)


def _category_profile(inputs: Mapping[str, Dict[str, Any]],
                      params: Mapping[str, Any],
                      narrate: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    listing = inputs["categories"]
    rows = listing["rows"]
    if not rows:
        return {"state": NOT_MEASURABLE, "reason": "empty", "measures": {"listed": 0},
                "rows": [], "narrative": narrate(reason="empty")}
    corpus_values = {int(r["corpus_contents"]) for r in rows}
    if len(corpus_values) != 1:
        raise AnalysisError(
            "category_profile: corpus_contents disagrees across category rows")
    corpus = corpus_values.pop()
    present_rows = [r for r in rows if int(r["contents"]) > 0]
    m = {
        "listed": len(rows), "present": len(present_rows), "corpus": corpus,
        "truncated": bool(listing["truncated"]),
    }
    measure = [
        ("measure.method", {}),
        ("measure.sizes", {"corpus": corpus, "listed": len(rows),
                           "present": len(present_rows)}),
        ("measure.truncated", {}) if listing["truncated"] else ("measure.complete", {}),
    ]
    if present_rows:
        top = present_rows[0]
        share = round(int(top["contents"]) / corpus * 100, 2) if corpus else 0.0
        finding = [("finding.top", {
            "name": top["category_name"], "contents": int(top["contents"]),
            "corpus": corpus, "share": share,
            "occurrences": int(top["occurrences"]),
            "members": int(top["member_words"])})]
        m.update({"top_name": top["category_name"], "top_share": share})
    else:
        finding = [("finding.none_present", {})]
    return {"state": MEASURED, "reason": None, "measures": m, "rows": [],
            "narrative": narrate(measure=measure, finding=finding)}


def _narrate_category(reason=None, measure=None, finding=None):
    T = CATEGORY_TEMPLATES
    if reason:
        finding_key = "finding.empty" if reason == "empty" else "finding.none_present"
        return T.compose([
            ("measure.not_measurable", {}), (finding_key, {}),
            ("confidence.not_measurable", {}), ("consequence.not_measurable", {}),
            ("caveat.not_measurable", {}),
        ])
    return T.compose([measure, finding, ("confidence.counts", {}),
                      ("consequence.read", {}), ("caveat.overlap", {})])


CATEGORY_PROFILE = Kind(
    name="category_profile", version=1,
    inputs={"categories": InputRole(
        ("capped",),
        ("category_name", "member_words", "contents", "occurrences",
         "corpus_contents"))},
    thresholds=(),
    templates=CATEGORY_TEMPLATES,
    compute=lambda inputs, params: _category_profile(inputs, params, _narrate_category),
)


PROFILE_KINDS: Tuple[Kind, ...] = (
    HORIZON_PROFILE, PLACE_PROFILE, KEYWORD_PROFILE, CATEGORY_PROFILE,
)

KINDS.update({f"{k.name}@{k.version}": k for k in PROFILE_KINDS})
