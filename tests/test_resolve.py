"""Entity resolution decides which company the rest of the dossier is about.

The behaviour worth protecting is the refusal: below 0.7 confidence resolve.py must hand
back candidates instead of picking one, because every pillar inherits the choice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import common  # noqa: E402
import resolve  # noqa: E402

ROUTES = {
    "wbsearchentities": "wikidata_search_infosys.json",
    "wbgetentities": "wikidata_entities_infosys.json",
    "company_tickers.json": "sec_company_tickers.json",
    "ListofScripData": "bse_scrip_master.json",
}


def test_company_name_normalisation():
    assert common.normalize_company_name("Acme Technologies Pvt. Ltd.") == "acme technologies"
    assert common.normalize_company_name("ACME INC") == common.normalize_company_name("Acme, Inc.")
    assert common.normalize_company_name("The Coca-Cola Company") == "coca cola"
    # A name that is only suffixes must not fold away to nothing.
    assert common.normalize_company_name("Limited") == "limited"


def test_title_normalisation_folds_seniority_and_noise():
    assert common.normalize_title("Sr. Software Engineer II (Remote)") == "senior software engineer"
    assert common.normalize_title("SDE-3, Backend") == common.normalize_title("Software Engineer 3 Backend")


def test_resolves_an_indian_listed_company_to_both_jurisdictions(fake_http):
    fake_http(ROUTES)
    entity = resolve.resolve("Infosys", None, "IN", None, "unused-cache-dir")
    ids = {e["id"] for e in entity["tree"]["entities"]}
    assert any(i.startswith("us:") for i in ids), ids
    assert any(i.startswith("in:") for i in ids), ids
    india = next(e for e in entity["tree"]["entities"] if e["jurisdiction"] == "IN")
    assert india["bse_scrip_code"] == "500209"
    assert india["cin"] == "L85110KA1981PLC013115"
    assert entity["confidence"] >= 0.7
    assert entity["resolved_entity_id"].startswith("in:"), "IN market should bind to the Indian entity"


def test_low_confidence_returns_candidates_instead_of_a_guess(fake_http):
    fake_http(ROUTES)
    entity = resolve.resolve("Totally Unrelated Widgets", None, "US", None, "unused-cache-dir")
    assert entity["confidence"] < 0.7
    assert entity["candidates"], "a low-confidence resolution must expose the alternatives"
    assert any("ask the user" in note for note in entity["notes"])


def test_scoring_prefers_a_domain_match_over_a_name_match():
    candidate_named = {"label": "Acme", "aliases": [], "official_site": "https://other.example"}
    candidate_domained = {"label": "Acme Holdings", "aliases": [], "official_site": "https://acme.com"}
    named = resolve._score(candidate_named, "Acme", "acme.com")
    domained = resolve._score(candidate_domained, "Acme", "acme.com")
    assert domained > named


def test_job_posting_parsing_extracts_employer_role_and_cin():
    html = """
    <html><head><title>Senior Backend Engineer - Acme</title>
    <link rel="canonical" href="https://careers.acme.com/jobs/42"></head>
    <body><h1>Senior Backend Engineer</h1>
    <p>Location: Bengaluru, India</p>
    <footer>Acme India Private Limited, CIN: U72200KA2011PTC057123</footer>
    </body></html>
    """
    # read_jd() fetches the page; the parsing it delegates to is what matters here.
    text = common.strip_html(html)
    assert "Acme India Private Limited" in text
    assert resolve.CIN_RE.search(text).group(0) == "U72200KA2011PTC057123"
    names = [" ".join(m.group(0).split()) for m in resolve.LEGAL_TAIL_RE.finditer(text)]
    assert any("Acme India Private Limited" in n for n in names)


def test_ats_hosts_are_not_mistaken_for_the_employer_domain():
    assert resolve._is_ats_host("boards.greenhouse.io")
    assert resolve._is_ats_host("acme.myworkdayjobs.com")
    assert not resolve._is_ats_host("careers.acme.com")


def test_offline_mode_raises_instead_of_fetching(tmp_path):
    with pytest.raises(common.SourceError) as excinfo:
        common.http_get("https://example.com/never-cached", ttl_seconds=60, cache_dir=tmp_path)
    assert "offline mode" in str(excinfo.value)


def test_api_endpoints_bypass_robots_but_ordinary_pages_do_not():
    assert common.is_api_endpoint("https://www.wikidata.org/w/api.php?action=wbsearchentities")
    assert common.is_api_endpoint("https://data.sec.gov/submissions/CIK0000320193.json")
    assert not common.is_api_endpoint("https://acme.com/careers")
    assert not common.is_api_endpoint("https://www.wikidata.org/wiki/Q42")
