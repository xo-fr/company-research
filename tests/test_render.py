"""The dossier has to work as a file: opened from disk, offline, years from now.

That means no external references, no build step, and an evidence block that survives
being embedded in HTML no matter what the sources put in their titles.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import common  # noqa: E402
import render  # noqa: E402

EVIDENCE = {
    "meta": {"generated_at": "2026-08-27T09:00:00Z", "stage": "applying", "spec_version": "1.0.0"},
    "entity": {
        "brand": "Acme",
        "domain": "acme.com",
        "resolved_entity_id": "us:0000000001",
        "employment_type": "gcc",
        "confidence": 0.88,
        "tree": {"brand": {"wikidata_qid": "Q1", "aliases": ["Acme"]},
                 "entities": [{"id": "us:0000000001", "jurisdiction": "US", "legal_name": "Acme Inc", "ticker": "ACME"}]},
    },
    "query": {"role": "Senior Backend Engineer", "market": "IN", "city": "Bengaluru",
              "jd_url": "https://careers.acme.com/jobs/42"},
    "sources": [
        {"id": "s1", "url": "https://www.sec.gov/x.htm", "publisher": "SEC EDGAR", "type": "filing",
         "published_at": "2026-02-14", "title": "Acme Inc 10-K </script> FY2025"},
        {"id": "s2", "url": "https://news.example.com/a", "publisher": "Example News", "type": "news",
         "published_at": "2026-06-01"},
    ],
    "pillars": {
        "overview": {"status": "ok", "claims": [
            {"id": "overview.0", "text": "Revenue is concentrated in two customers.",
             "source_ids": ["s1"], "confidence": "high"}]},
        "culture": {"status": "gap", "claims": [], "gaps": [
            {"reason": "no free source for this private company", "suggested_fallback": "web search"}]},
    },
    "signals": {
        "revenue_trend": {"value": "growing", "confidence": "high", "source_ids": ["s1"]},
        "layoff_events_24m": {"value": 1, "confidence": "high", "source_ids": ["s2"]},
        "rating_current": {"value": None, "confidence": "none"},
    },
    "narrative": {
        "summary": "Growing, concentrated, and hiring in Bengaluru.",
        "strengths": [{"text": "Revenue growth", "claim_ids": ["overview.0"]}],
        "concerns": [{"text": "Customer concentration", "claim_ids": ["overview.0"]}],
        "questions_to_ask": ["How large is the India platform team?"],
    },
    "profile": {"priorities": ["stability", "comp", "wlb"], "work_authorization": {"IN": "citizen"}},
}


@pytest.fixture
def page() -> str:
    return render.render(EVIDENCE)


def test_placeholders_are_all_substituted(page):
    for placeholder in ("__TITLE__", "__EVIDENCE_JSON__", "__SIGNALS_JSON__"):
        assert placeholder not in page
    assert "<title>Acme · Senior Backend Engineer</title>" in page


def test_page_is_self_contained(page):
    """No CDN, no external stylesheet, no fetch: it must render from a file:// URL."""
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    assert external == [], f"dossier must not reference external resources: {external}"
    assert "fetch(" not in page
    assert "XMLHttpRequest" not in page


def test_evidence_round_trips_through_the_embedded_block(page):
    block = re.search(r'<script type="application/json" id="evidence-data">(.*?)</script>', page, re.S)
    assert block, "evidence block missing"
    restored = json.loads(block.group(1).replace("<\\/", "</"))
    assert restored["entity"]["brand"] == "Acme"
    assert restored["pillars"]["culture"]["status"] == "gap"


def test_a_source_title_containing_a_script_tag_cannot_break_out(page):
    # The fixture's first source title contains a literal </script>.
    head, _, tail = page.partition('<script type="application/json" id="evidence-data">')
    embedded, _, _ = tail.partition("</script>")
    assert "10-K" in embedded, "the escaped title must stay inside the JSON block"


def test_registry_travels_with_the_page(page):
    block = re.search(r'<script type="application/json" id="signal-registry">(.*?)</script>', page, re.S)
    registry = json.loads(block.group(1).replace("<\\/", "</"))
    assert len(registry["signals"]) == 14
    assert registry["dimensions"] == common.DIMENSIONS
    assert set(registry["pillar_labels"]) == set(common.PILLARS)


def test_coverage_string_is_literal_and_computed_in_the_page(page):
    assert '"computed from " + cov.scored + " of " + cov.total + " signals"' in page


def test_gap_pillars_get_an_explicit_empty_state(page):
    assert "gapbox" in page
    assert "Nothing found, and no reason recorded" in page


def test_writes_a_file_and_reports_it(tmp_path):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(EVIDENCE), encoding="utf-8")
    out = tmp_path / "dossier.html"
    exit_code = render.main(["--evidence", str(evidence_path), "--out", str(out)])
    assert exit_code == 0
    assert out.exists() and out.stat().st_size > 10_000


def test_verdict_and_weights_behave_in_the_browser(tmp_path, node_available):
    """Drive the page's own scoring core the way a slider drag would."""
    if not node_available:
        pytest.skip("node not installed")
    html = render.render(EVIDENCE)
    core = re.search(r'<script id="cr-scoring">(.*?)</script>', html, re.S).group(1)
    (tmp_path / "cr.js").write_text(core, encoding="utf-8")
    (tmp_path / "ev.json").write_text(json.dumps(EVIDENCE), encoding="utf-8")
    (tmp_path / "reg.json").write_text(json.dumps(render.registry()), encoding="utf-8")
    (tmp_path / "run.js").write_text(
        """
        const CR = require('./cr.js');
        const EV = require('./ev.json');
        const REG = require('./reg.json');
        const sponsored = CR.needsSponsorship(EV.profile);
        const dims = CR.dimensionScores(EV, REG, sponsored);
        const heavyStability = CR.verdict(dims, {stability: 100, comp: 5, wlb: 5, learning: 5, growth: 5, logistics: 5}, REG.dimensions);
        const flat = CR.verdict(dims, {stability: 50, comp: 50, wlb: 50, learning: 50, growth: 50, logistics: 50}, REG.dimensions);
        console.log(JSON.stringify({
          dims, sponsored,
          coverage: CR.coverage(EV, REG, sponsored),
          heavyStability, flat,
          defaults: CR.defaultWeights(EV.profile, REG, sponsored)
        }));
        """,
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(tmp_path / "run.js")], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    # revenue_trend growing (1.0) and layoff_events_24m = 1 (0.6) -> stability 0.8
    assert out["dims"]["stability"] == pytest.approx(0.8)
    assert out["dims"]["wlb"] is None, "a 'none' signal must not be scored"
    assert out["dims"]["comp"] is None
    assert out["coverage"] == {"scored": 2, "total": 14}
    assert out["sponsored"] is False
    assert out["defaults"]["stability"] > out["defaults"]["logistics"]
    # only one dimension has evidence, so the weighted mean is that dimension either way
    assert out["heavyStability"] == pytest.approx(out["flat"])


def test_dimension_with_no_evidence_is_excluded_not_zeroed(tmp_path, node_available):
    if not node_available:
        pytest.skip("node not installed")
    evidence = json.loads(json.dumps(EVIDENCE))
    evidence["signals"]["rating_current"] = {"value": 3.0, "confidence": "medium", "source_ids": ["s2"]}
    html = render.render(evidence)
    core = re.search(r'<script id="cr-scoring">(.*?)</script>', html, re.S).group(1)
    (tmp_path / "cr.js").write_text(core, encoding="utf-8")
    (tmp_path / "ev.json").write_text(json.dumps(evidence), encoding="utf-8")
    (tmp_path / "reg.json").write_text(json.dumps(render.registry()), encoding="utf-8")
    (tmp_path / "run.js").write_text(
        """
        const CR = require('./cr.js'); const EV = require('./ev.json'); const REG = require('./reg.json');
        const dims = CR.dimensionScores(EV, REG, false);
        const w = {stability: 100, comp: 100, wlb: 100, learning: 100, growth: 100, logistics: 100};
        console.log(JSON.stringify({dims, verdict: CR.verdict(dims, w, REG.dimensions)}));
        """,
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(tmp_path / "run.js")], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    out = json.loads(result.stdout)
    # stability 0.8, wlb 0.25 -> mean of the two scored dimensions, not of all six
    assert out["verdict"] == pytest.approx((0.8 + 0.25) / 2)
