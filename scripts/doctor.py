"""Probe every upstream source and report live / degraded / dead.

Sources rot silently. This is the one command that tells a user (or a nightly CI job)
which half of the dossier is going to come back empty and why.

    python scripts/doctor.py [--only sec,wikidata] [--pretty]

Exit 0 when every *core* source is live. Exit 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402


# api.bseindia.com 406s anything that does not look like the site's own XHR.
BSE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}


def _months_ago(months: int) -> str:
    from datetime import date

    today = date.today()
    month = today.month - months
    year = today.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}{month:02d}{min(today.day, 28):02d}"


PROBE_TIMEOUT = 20.0
PROBE_ATTEMPTS = 2


def _probe_json(url: str, ttl: int, check, headers=None, cache_dir=None):
    resp = common.http_get(
        url, ttl_seconds=ttl, headers=headers, cache_dir=cache_dir,
        timeout=PROBE_TIMEOUT, attempts=PROBE_ATTEMPTS,
    )
    data = resp.json()
    ok, detail = check(data)
    return ok, detail


def _probe_text(url: str, ttl: int, check, headers=None, cache_dir=None):
    resp = common.http_get(
        url, ttl_seconds=ttl, headers=headers, cache_dir=cache_dir,
        timeout=PROBE_TIMEOUT, attempts=PROBE_ATTEMPTS,
    )
    ok, detail = check(resp.text)
    return ok, detail


def _checks(cache_dir):
    """Each entry: id, name, core, fix hint, and a zero-arg callable -> (ok, detail)."""
    sec_hdrs = None
    sec_hint = (
        "SEC requires a contact address in the User-Agent. Set CR_CONTACT_EMAIL or add "
        "contact_email: to ~/.company-research/profile.yaml"
    )
    try:
        sec_hdrs = common.sec_headers()
        sec_hdr_error = None
    except common.MissingContactEmail as exc:
        sec_hdr_error = str(exc)

    def sec_tickers():
        if sec_hdr_error:
            raise common.SourceError("no contact email configured")
        return _probe_json(
            "https://www.sec.gov/files/company_tickers.json",
            common.cache_ttl("entity"),
            lambda d: (len(d) > 1000, f"{len(d)} tickers"),
            headers=sec_hdrs,
            cache_dir=cache_dir,
        )

    def sec_submissions():
        if sec_hdr_error:
            raise common.SourceError("no contact email configured")
        return _probe_json(
            "https://data.sec.gov/submissions/CIK0000320193.json",  # Apple
            common.cache_ttl("filings"),
            lambda d: (
                bool(d.get("filings", {}).get("recent", {}).get("form")),
                f"{d.get('name', '?')}: {len(d.get('filings', {}).get('recent', {}).get('form', []))} recent filings",
            ),
            headers=sec_hdrs,
            cache_dir=cache_dir,
        )

    def wikidata_search():
        return _probe_json(
            "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=Infosys"
            "&language=en&format=json&limit=3",
            common.cache_ttl("entity"),
            lambda d: (bool(d.get("search")), f"{len(d.get('search', []))} hits"),
            cache_dir=cache_dir,
        )

    def wikidata_sparql():
        query = "SELECT ?item WHERE { ?item wdt:P31 wd:Q4830453 } LIMIT 1"
        url = (
            "https://query.wikidata.org/sparql?format=json&query="
            + common.urllib.parse.quote(query)
        )
        return _probe_json(
            url,
            common.cache_ttl("entity"),
            lambda d: (
                bool(d.get("results", {}).get("bindings")),
                f"{len(d.get('results', {}).get('bindings', []))} rows",
            ),
            headers={"Accept": "application/sparql-results+json"},
            cache_dir=cache_dir,
        )

    def gdelt():
        return _probe_json(
            "https://api.gdeltproject.org/api/v2/doc/doc?query=%22Infosys%22"
            "&mode=artlist&maxrecords=5&format=json",
            common.cache_ttl("news"),
            lambda d: (bool(d.get("articles")), f"{len(d.get('articles', []))} articles"),
            cache_dir=cache_dir,
        )

    def hn_algolia():
        return _probe_json(
            "https://hn.algolia.com/api/v1/search?query=Infosys&hitsPerPage=5",
            common.cache_ttl("interview"),
            lambda d: (bool(d.get("hits")), f"{d.get('nbHits', 0)} hits"),
            cache_dir=cache_dir,
        )

    def wayback():
        return _probe_json(
            "http://web.archive.org/cdx/search/cdx?url=stripe.com/jobs*&output=json"
            "&limit=5&collapse=timestamp:6",
            common.cache_ttl("snapshots"),
            lambda d: (len(d) > 1, f"{max(len(d) - 1, 0)} captures"),
            cache_dir=cache_dir,
        )

    def jina():
        return _probe_text(
            "https://r.jina.ai/https://example.com",
            common.cache_ttl("default"),
            lambda t: (len(t.strip()) > 100, f"{len(t.strip())} chars"),
            headers={"Accept": "text/plain"},
            cache_dir=cache_dir,
        )

    def dol():
        return _probe_text(
            "https://www.dol.gov/agencies/eta/foreign-labor/performance",
            common.cache_ttl("compensation"),
            lambda t: (
                "LCA" in t or "Disclosure" in t or "H-1B" in t,
                "disclosure index reachable",
            ),
            cache_dir=cache_dir,
        )

    def github():
        return _probe_json(
            "https://api.github.com/rate_limit",
            60,
            lambda d: (
                d.get("resources", {}).get("core", {}).get("limit", 0) > 0,
                f"{d['resources']['core']['remaining']}/{d['resources']['core']['limit']} calls left",
            ),
            headers={"Accept": "application/vnd.github+json"},
            cache_dir=cache_dir,
        )

    def bse():
        # Reliance Industries (scrip 500325), announcements in a wide window.
        url = (
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1"
            "&strCat=-1&strPrevDate=" + _months_ago(6) + "&strScrip=500325&strSearch=P"
            "&strToDate=" + common.today().replace("-", "") + "&strType=C&subcategory=-1"
        )
        return _probe_json(
            url,
            common.cache_ttl("filings"),
            lambda d: (
                isinstance(d, dict) and bool(d.get("Table")),
                f"{len(d.get('Table', [])) if isinstance(d, dict) else 0} announcements",
            ),
            headers=BSE_HEADERS,
            cache_dir=cache_dir,
        )

    return [
        ("sec_tickers", "SEC ticker map", True, sec_hint, sec_tickers),
        ("sec_submissions", "SEC submissions API", True, sec_hint, sec_submissions),
        ("wikidata_search", "Wikidata search", True, "wikidata.org may be rate limiting; retry", wikidata_search),
        ("wikidata_sparql", "Wikidata SPARQL", False, "SPARQL endpoint throttles hard; entity resolution degrades to search only", wikidata_sparql),
        ("gdelt", "GDELT news", True, "GDELT 2.0 doc API changes shape occasionally; check api.gdeltproject.org", gdelt),
        ("hn_algolia", "HN Algolia", False, "hn.algolia.com is the tech-sentiment source; culture pillar degrades without it", hn_algolia),
        ("wayback", "Wayback CDX", False, "CDX is slow under load; hiring_trend falls back to local snapshots", wayback),
        ("jina", "Jina Reader", False, "optional: extract_text falls back to the local extractor", jina),
        ("dol", "DOL H-1B disclosure index", False, "US compensation and sponsorship signals go dark without it", dol),
        ("github", "GitHub API", False, "unauthenticated limit is 60/hr; set GITHUB_TOKEN to raise it", github),
        ("bse", "BSE India API", False, "India listed filings degrade to web search; unlisted are a known gap", bse),
    ]


def run(only: set[str] | None, cache_dir) -> dict:
    results = []
    for cid, name, core, hint, fn in _checks(cache_dir):
        if only and cid not in only:
            continue
        started = time.monotonic()
        status, detail = "dead", ""
        try:
            ok, detail = fn()
            status = "live" if ok else "degraded"
        except common.MissingContactEmail as exc:
            status, detail = "dead", str(exc).splitlines()[0]
        except common.SourceError as exc:
            status, detail = "dead", str(exc)
        except Exception as exc:  # malformed payload = the shape changed upstream
            status, detail = "degraded", f"{type(exc).__name__}: {exc}"
        results.append(
            {
                "id": cid,
                "name": name,
                "core": core,
                "status": status,
                "detail": detail,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "hint": hint if status != "live" else "",
            }
        )
    summary = {
        "live": sum(1 for r in results if r["status"] == "live"),
        "degraded": sum(1 for r in results if r["status"] == "degraded"),
        "dead": sum(1 for r in results if r["status"] == "dead"),
        "core_ok": all(r["status"] == "live" for r in results if r["core"]),
    }
    return {"generated_at": common.iso_now(), "results": results, "summary": summary}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated probe ids to run")
    parser.add_argument("--quiet", action="store_true", help="JSON only, no human summary on stderr")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    report = run(only, args.cache_dir)

    common.emit(report, args.pretty)
    if not args.quiet:
        icons = {"live": "OK  ", "degraded": "WARN", "dead": "DEAD"}
        for r in report["results"]:
            line = f"{icons[r['status']]} {r['name']:<32} {r['latency_ms']:>5}ms  {r['detail']}"
            print(line, file=sys.stderr)
            if r["hint"]:
                print(f"      hint: {r['hint']}", file=sys.stderr)
        s = report["summary"]
        print(
            f"\n{s['live']} live, {s['degraded']} degraded, {s['dead']} dead"
            f" — core sources {'OK' if s['core_ok'] else 'NOT OK'}",
            file=sys.stderr,
        )
    return 0 if report["summary"]["core_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
