"""The signal registry is the contract between the extractor and the verdict.

Two implementations read it: ``common.normalize_signal`` in Python and the ``CR`` core in
the dashboard. If they ever disagree, the number in the dossier stops meaning what the
tests say it means -- so they are checked against each other here, on the same table of
cases taken from BUILD-SPEC section 6.
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

CASES = [
    ("layoff_events_24m", 0, 1.0),
    ("layoff_events_24m", 1, 0.6),
    ("layoff_events_24m", 2, 0.3),
    ("layoff_events_24m", 3, 0.1),
    ("layoff_events_24m", 9, 0.1),
    ("funding_months_ago", 0, 1.0),
    ("funding_months_ago", 11, 1.0),
    ("funding_months_ago", 12, 0.7),
    ("funding_months_ago", 23, 0.7),
    ("funding_months_ago", 24, 0.4),
    ("funding_months_ago", 36, 0.15),
    ("revenue_trend", "growing", 1.0),
    ("revenue_trend", "flat", 0.5),
    ("revenue_trend", "declining", 0.1),
    ("role_repost_count_12m", 1, 1.0),
    ("role_repost_count_12m", 2, 0.7),
    ("role_repost_count_12m", 3, 0.4),
    ("role_repost_count_12m", 4, 0.15),
    ("hiring_velocity_90d", 0.0, 0.5),
    ("hiring_velocity_90d", 0.25, 0.75),
    ("hiring_velocity_90d", -0.75, 0.0),
    ("hiring_velocity_90d", 2.0, 1.0),
    ("headcount_trend_12m", 0.0, 0.5),
    ("headcount_trend_12m", 0.1, 0.7),
    ("headcount_trend_12m", -0.2, 0.1),
    ("comp_percentile_vs_market", 0, 0.0),
    ("comp_percentile_vs_market", 72, 0.72),
    ("comp_percentile_vs_market", 100, 1.0),
    ("comp_transparency", True, 1.0),
    ("comp_transparency", False, 0.0),
    ("rating_current", 2.5, 0.0),
    ("rating_current", 3.5, 0.5),
    ("rating_current", 4.5, 1.0),
    ("rating_current", 1.0, 0.0),
    ("rating_trend_24m", 0.0, 0.5),
    ("rating_trend_24m", -0.4, 0.1),
    ("rating_trend_24m", 0.3, 0.8),
    ("wlb_sentiment", -1.0, 0.0),
    ("wlb_sentiment", 0.0, 0.5),
    ("wlb_sentiment", 1.0, 1.0),
    ("eng_output_signal", 0.35, 0.35),
    ("stack_currency", 0.9, 0.9),
    ("sponsorship_history_3y", 0, 0.0),
    ("sponsorship_history_3y", 1, 0.5),
    ("sponsorship_history_3y", 9, 0.5),
    ("sponsorship_history_3y", 10, 0.8),
    ("sponsorship_history_3y", 49, 0.8),
    ("sponsorship_history_3y", 50, 1.0),
]


@pytest.mark.parametrize("signal_id,raw,expected", CASES)
def test_normalisation_matches_the_spec(signal_id, raw, expected):
    assert common.normalize_signal(signal_id, raw) == pytest.approx(expected)


def test_registry_is_complete_and_well_formed():
    assert len(common.SIGNALS) == 14, "the spec defines exactly 14 signals"
    for signal_id, spec in common.SIGNALS.items():
        assert spec["dimension"] in common.DIMENSIONS, signal_id
        assert spec["direction"] in ("higher_better", "lower_better"), signal_id
        assert spec["normalize"]["kind"] in ("affine", "table", "enum"), signal_id
        assert spec.get("label"), f"{signal_id} needs a human label for the dashboard"
    assert set(common.DIMENSION_LABELS) == set(common.DIMENSIONS)


def test_every_dimension_has_at_least_one_signal():
    covered = {spec["dimension"] for spec in common.SIGNALS.values()}
    assert covered == set(common.DIMENSIONS)


def test_unknown_signal_and_unusable_value_normalise_to_none():
    assert common.normalize_signal("not_a_signal", 1) is None
    assert common.normalize_signal("layoff_events_24m", None) is None
    assert common.normalize_signal("layoff_events_24m", "many") is None
    assert common.normalize_signal("revenue_trend", "sideways") is None


def _scoring_core() -> str:
    html = (Path(__file__).resolve().parent.parent / "templates" / "dossier.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="cr-scoring">(.*?)</script>', html, re.S)
    assert match, "the dashboard must keep its scoring core in a script#cr-scoring block"
    return match.group(1)


def test_browser_and_python_normalisers_agree(tmp_path, node_available):
    """Run the dashboard's own JavaScript against the same table."""
    if not node_available:
        pytest.skip("node not installed")
    (tmp_path / "cr.js").write_text(_scoring_core(), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps(render.registry()), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps([[c[0], c[1]] for c in CASES]), encoding="utf-8")
    (tmp_path / "run.js").write_text(
        "const CR = require('./cr.js');\n"
        "const REG = require('./registry.json');\n"
        "const cases = require('./cases.json');\n"
        "console.log(JSON.stringify(cases.map(([id, raw]) => CR.normalize(REG.signals[id], raw))));\n",
        encoding="utf-8",
    )
    output = subprocess.run(
        ["node", str(tmp_path / "run.js")], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert output.returncode == 0, output.stderr
    from_js = json.loads(output.stdout)
    for (signal_id, raw, expected), got in zip(CASES, from_js):
        assert got == pytest.approx(expected), f"{signal_id}({raw}) = {got} in the browser, {expected} in the spec"


def test_stage_pillar_sets_match_the_spec():
    assert common.STAGE_PILLARS["applying"] == [
        "overview", "news", "financial_health", "culture", "hiring_trend",
    ]
    assert common.STAGE_PILLARS["interviewing"] == [
        "overview", "interview_prep", "jd_gap", "interviewers", "culture", "news",
    ]
    assert common.STAGE_PILLARS["offer"] == [
        "compensation", "financial_health", "reviews", "hiring_trend", "culture",
    ]
    for pillars in common.STAGE_PILLARS.values():
        assert set(pillars) <= set(common.PILLARS)
