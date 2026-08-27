"""merge.py is the only thing standing between a subagent's mistake and a dossier that
looks authoritative while citing nothing. These tests are mostly about what it refuses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge  # noqa: E402


def fragment(**overrides) -> dict:
    base = {
        "entity": {"brand": "Acme", "domain": "acme.com", "employment_type": "direct", "confidence": 0.92},
        "query": {"role": "Senior Backend Engineer", "market": "US"},
        "sources": [
            {
                "id": "s1",
                "url": "https://www.sec.gov/Archives/edgar/data/1/acme-10k.htm",
                "publisher": "SEC EDGAR",
                "type": "filing",
                "published_at": "2026-02-14",
            }
        ],
        "pillars": {
            "overview": {
                "status": "ok",
                "entity_id": "us:0000000001",
                "claims": [
                    {
                        "id": "overview.0",
                        "text": "Revenue is concentrated in two enterprise customers.",
                        "source_ids": ["s1"],
                        "confidence": "high",
                    }
                ],
            }
        },
        "signals": {"revenue_trend": {"value": "growing", "confidence": "high", "source_ids": ["s1"]}},
    }
    base.update(overrides)
    return base


def test_merges_and_writes_a_valid_document(dossier):
    dossier.write("overview", fragment())
    document, errors = merge.merge(dossier.path, None, "applying")
    assert errors == []
    assert document["meta"]["stage"] == "applying"
    assert document["entity"]["brand"] == "Acme"
    assert document["pillars"]["overview"]["claims"][0]["source_ids"] == ["s1"]
    assert merge.coverage(document)["signals_total"] == 14


def test_sources_are_deduplicated_across_fragments_and_ids_rewritten(dossier):
    dossier.write("overview", fragment())
    dossier.write(
        "news",
        {
            "sources": [
                {"id": "s1", "url": "https://news.example.com/a?utm_source=x", "publisher": "Example", "type": "news"},
                # same document as the overview fragment's s1, with tracking noise and www
                {"id": "s2", "url": "https://www.sec.gov/Archives/edgar/data/1/acme-10k.htm?ref=y",
                 "publisher": "SEC EDGAR", "type": "filing"},
            ],
            "pillars": {
                "news": {
                    "status": "ok",
                    "claims": [
                        {"id": "news.0", "text": "Announced a 5% reduction.", "source_ids": ["s1", "s2"],
                         "confidence": "medium"}
                    ],
                }
            },
        },
    )
    document, errors = merge.merge(dossier.path, None, "applying")
    assert errors == []
    urls = [s["url"] for s in document["sources"]]
    assert len(urls) == 2, f"the SEC filing should appear once, got {urls}"

    news_refs = document["pillars"]["news"]["claims"][0]["source_ids"]
    overview_refs = document["pillars"]["overview"]["claims"][0]["source_ids"]
    by_id = {s["id"]: s["url"] for s in document["sources"]}
    assert "sec.gov" in by_id[overview_refs[0]]
    assert by_id[news_refs[1]] == by_id[overview_refs[0]], "both fragments must land on one id"


def test_dangling_source_reference_is_reported_and_nothing_is_written(dossier):
    frag = fragment()
    frag["pillars"]["overview"]["claims"][0]["source_ids"] = ["s7"]
    dossier.write("overview", frag)
    document, errors = merge.merge(dossier.path, None, None)
    assert any("s7" in e for e in errors)
    assert not (dossier.path / "evidence.json").exists()


def test_empty_pillar_must_be_a_gap_with_a_reason(dossier):
    dossier.write("overview", fragment())
    dossier.write("culture", {"pillars": {"culture": {"status": "ok", "claims": []}}})
    _, errors = merge.merge(dossier.path, None, None)
    assert any("status must be 'gap'" in e for e in errors)
    assert any("no gaps explaining why" in e for e in errors)


def test_declared_gap_with_a_reason_passes(dossier):
    dossier.write("overview", fragment())
    dossier.write(
        "compensation",
        {
            "pillars": {
                "compensation": {
                    "status": "gap",
                    "gaps": [
                        {
                            "reason": "no free structured compensation source exists for the Indian market",
                            "suggested_fallback": "web search AmbitionBox for this title",
                        }
                    ],
                }
            }
        },
    )
    document, errors = merge.merge(dossier.path, None, "offer")
    assert errors == []
    assert document["pillars"]["compensation"]["status"] == "gap"


def test_signal_confidence_and_value_must_agree(dossier):
    frag = fragment()
    frag["signals"] = {
        "rating_current": {"value": None, "confidence": "high"},
        "comp_percentile_vs_market": {"value": 55, "confidence": "none"},
        "layoff_events_24m": {"value": "several", "confidence": "medium"},
    }
    dossier.write("overview", frag)
    _, errors = merge.merge(dossier.path, None, None)
    assert any("rating_current" in e and "confidence 'none'" in e for e in errors)
    assert any("comp_percentile_vs_market" in e and "value null" in e for e in errors)
    assert any("layoff_events_24m" in e and "cannot be normalised" in e for e in errors)


def test_unknown_pillar_and_signal_names_are_rejected(dossier):
    dossier.write("overview", fragment())
    dossier.write(
        "weird",
        {
            "pillars": {"vibes": {"status": "ok", "claims": []}},
            "signals": {"vibe_score": {"value": 1, "confidence": "high"}},
        },
    )
    _, errors = merge.merge(dossier.path, None, None)
    assert any("unknown pillar 'vibes'" in e for e in errors)
    assert any("unknown signal 'vibe_score'" in e for e in errors)


def test_narrative_must_cite_real_claims(dossier):
    dossier.write("overview", fragment())
    (dossier.path / "evidence" / "narrative.json").write_text(
        json.dumps(
            {
                "narrative": {
                    "summary": "Concentrated but growing.",
                    "strengths": [{"text": "Growth", "claim_ids": ["overview.0"]}],
                    "concerns": [{"text": "Ghost", "claim_ids": ["news.9"]}],
                }
            }
        ),
        encoding="utf-8",
    )
    _, errors = merge.merge(dossier.path, None, None)
    assert any("news.9" in e for e in errors)


def test_narrative_is_folded_in_when_valid(dossier):
    dossier.write("overview", fragment())
    (dossier.path / "evidence" / "narrative.json").write_text(
        json.dumps({"narrative": {"summary": "Fine.", "strengths": [{"text": "Growth", "claim_ids": ["overview.0"]}]}}),
        encoding="utf-8",
    )
    document, errors = merge.merge(dossier.path, None, None)
    assert errors == []
    assert document["narrative"]["summary"] == "Fine."


def test_claim_ids_are_renamed_into_the_pillar_namespace(dossier):
    frag = fragment()
    frag["pillars"]["overview"]["claims"][0]["id"] = "whatever-the-model-felt-like"
    dossier.write("overview", frag)
    document, errors = merge.merge(dossier.path, None, None)
    assert errors == []
    assert document["pillars"]["overview"]["claims"][0]["id"] == "overview.0"


def test_two_fragments_cannot_set_the_same_signal(dossier):
    dossier.write("overview", fragment())
    dossier.write(
        "financial_health",
        {
            "sources": [{"id": "s1", "url": "https://x.example/1", "publisher": "X", "type": "filing"}],
            "pillars": {
                "financial_health": {
                    "status": "ok",
                    "claims": [{"id": "financial_health.0", "text": "Cash is falling.", "source_ids": ["s1"],
                                "confidence": "high"}],
                }
            },
            "signals": {"revenue_trend": {"value": "declining", "confidence": "high", "source_ids": ["s1"]}},
        },
    )
    _, errors = merge.merge(dossier.path, None, None)
    assert any("already set by another fragment" in e for e in errors)


def test_canonical_url_folding():
    same = merge._canonical_url("https://WWW.Example.com/a/b/?utm_source=x&q=1")
    assert same == merge._canonical_url("https://example.com/a/b?q=1")
    assert merge._canonical_url("https://example.com/a") != merge._canonical_url("https://example.com/b")


def test_validator_rejects_wrong_types_and_unknown_properties():
    schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "evidence.schema.json").read_text(encoding="utf-8"))
    errors = merge.validate(
        {"id": "s1", "url": 42, "type": "blog"}, schema["$defs"]["source"], schema
    )
    assert any("expected string" in e for e in errors)
    assert any("not one of" in e for e in errors)
