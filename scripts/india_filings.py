"""Corporate disclosures for an Indian company, from BSE.

    python scripts/india_filings.py --name "Infosys"
    python scripts/india_filings.py --scrip 500325 --months 6 --limit 20
    python scripts/india_filings.py --name "Acme India Pvt Ltd"   # -> status: gap

Covers BSE-listed entities: announcements (Reg 30 disclosures, results, board meetings),
annual report PDFs, and the exchange's own headline ratios.

Unlisted Indian private limited companies -- which is most GCCs and every startup before
IPO -- have no free structured source. MCA charges per document and has no API. This
script says so explicitly rather than guessing: the evidence file gets a ``gap`` with a
fallback the agent can act on, which is more useful than a confident wrong number.

NSE is deliberately not used: its API requires a browser cookie handshake, and nearly
every NSE-listed company of interest is also on BSE.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import resolve as resolve_mod  # noqa: E402

API = "https://api.bseindia.com/BseIndiaAPI/api"
HEADERS = resolve_mod.BSE_HEADERS
ATTACH_LIVE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
ATTACH_HIS = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"

# Announcement categories worth flagging to a job seeker, in priority order.
HIGH_SIGNAL = (
    ("result", "quarterly or annual results"),
    ("resignation", "senior departure"),
    ("appointment", "senior appointment"),
    ("fund raising", "fundraising"),
    ("acquisition", "M&A"),
    ("scheme of arrangement", "restructuring"),
    ("closure", "unit or subsidiary closure"),
    ("layoff", "workforce reduction"),
    ("insolvency", "insolvency proceedings"),
    ("credit rating", "credit rating change"),
)


def _yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _get(path: str, params: dict, cache_dir, ttl: int | None = None):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    return common.http_get(
        url,
        ttl_seconds=ttl if ttl is not None else common.cache_ttl("filings"),
        headers=HEADERS,
        cache_dir=cache_dir,
        timeout=45,
    )


def announcements(scrip: str, months: int, limit: int, cache_dir) -> list[dict]:
    today = date.today()
    start = today - timedelta(days=30 * months)
    rows: list[dict] = []
    for page in range(1, 6):  # BSE pages 50 at a time
        try:
            data = _get(
                "AnnSubCategoryGetData/w",
                {
                    "pageno": page,
                    "strCat": -1,
                    "strPrevDate": _yyyymmdd(start),
                    "strScrip": scrip,
                    "strSearch": "P",
                    "strToDate": _yyyymmdd(today),
                    "strType": "C",
                    "subcategory": -1,
                },
                cache_dir,
                ttl=common.cache_ttl("news"),
            ).json()
        except (common.SourceError, ValueError):
            break
        if not isinstance(data, dict):
            break
        table = data.get("Table") or []
        if not table:
            break
        for row in table:
            attachment = (row.get("ATTACHMENTNAME") or "").strip()
            subject = (row.get("NEWSSUB") or "").strip()
            headline = common.strip_html(row.get("HEADLINE") or "").strip()
            rows.append(
                {
                    "date": (row.get("NEWS_DT") or "")[:10],
                    "category": (row.get("CATEGORYNAME") or row.get("ANNOUNCEMENT_TYPE") or "").strip(),
                    "subject": subject,
                    "headline": headline[:600],
                    "pdf_url": ATTACH_LIVE + attachment if attachment else None,
                    "flags": [
                        label
                        for needle, label in HIGH_SIGNAL
                        if needle in f"{subject} {headline}".lower()
                    ],
                }
            )
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]


def annual_reports(scrip: str, cache_dir, limit: int = 6) -> list[dict]:
    try:
        data = _get("AnnualReport_New/w", {"scripcode": scrip}, cache_dir).json()
    except (common.SourceError, ValueError):
        return []
    out = []
    for row in (data.get("Table") or []) if isinstance(data, dict) else []:
        url = (row.get("PDFDownload") or "").strip()
        if not url:
            continue
        out.append({"year": str(row.get("Year", "")).strip(), "url": url})
    # BSE sometimes lists two files for one year (report + notice); keep the first each.
    seen, deduped = set(), []
    for row in out:
        if row["year"] in seen:
            continue
        seen.add(row["year"])
        deduped.append(row)
    return deduped[:limit]


def headline_metrics(scrip: str, cache_dir) -> dict:
    try:
        data = _get("ComHeadernew/w", {"quotetype": "EQ", "scripcode": scrip, "seriesid": ""}, cache_dir).json()
    except (common.SourceError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    keep = {
        "SecurityId": "ticker",
        "ISIN": "isin",
        "Industry": "industry",
        "Group": "bse_group",
        "Index": "index_membership",
        "EPS": "eps",
        "PE": "pe",
        "OPM": "operating_margin_pct",
        "NPM": "net_margin_pct",
        "ROE": "roe_pct",
        "PB": "price_to_book",
        "MCAP": "market_cap_cr",
    }
    return {v: str(data[k]).strip() for k, v in keep.items() if data.get(k) not in (None, "")}


def run(name: str | None, scrip: str | None, cin: str | None, months: int, limit: int, cache_dir) -> dict:
    entity: dict = {"legal_name": name, "cin": cin, "scrip_code": scrip}
    gaps: list[dict] = []

    if not scrip and name:
        match = resolve_mod.bse_scrip(name, cache_dir)
        if match:
            scrip = match["scrip_code"]
            entity.update(
                {"legal_name": match["legal_name"], "scrip_code": scrip, "isin": match["isin"]}
            )

    if not scrip:
        return {
            "status": "gap",
            "entity": entity,
            "announcements": [],
            "annual_reports": [],
            "gaps": [
                {
                    "reason": (
                        f"{name or cin} is not BSE-listed, so no exchange disclosures exist. "
                        "MCA (the Indian company registry) has no free API and charges per "
                        "document, so unlisted Indian entities have no structured free source."
                    ),
                    "suggested_fallback": (
                        "search for the parent company's filings if this is a subsidiary of a "
                        "listed or US-filing group; otherwise search news and the company's own "
                        "press releases, and mark financial_health partial"
                    ),
                }
            ],
            "retrieved_at": common.iso_now(),
        }

    anns = announcements(scrip, months, limit, cache_dir)
    reports = annual_reports(scrip, cache_dir)
    metrics = headline_metrics(scrip, cache_dir)
    if metrics.get("isin"):
        entity.setdefault("isin", metrics["isin"])
    if not anns:
        gaps.append(
            {
                "reason": f"no BSE announcements in the last {months} months for scrip {scrip}",
                "suggested_fallback": "widen --months, or read the latest annual report instead",
            }
        )

    return {
        "status": "ok" if anns or reports else "partial",
        "entity": entity,
        "headline_metrics": metrics,
        "announcements": anns,
        "annual_reports": reports,
        "flagged": [a for a in anns if a["flags"]][:15],
        "gaps": gaps,
        "source_url": f"https://www.bseindia.com/stock-share-price/x/x/{scrip}/corp-announcements/",
        "retrieved_at": common.iso_now(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", help="legal or brand name")
    parser.add_argument("--scrip", help="BSE scrip code, if already known")
    parser.add_argument("--cin", help="Corporate Identification Number, for the record")
    parser.add_argument("--months", type=int, default=12, help="announcement window (default 12)")
    parser.add_argument("--limit", type=int, default=25)
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    if not (args.name or args.scrip or args.cin):
        common.fail("india_filings.py: pass --name, --scrip or --cin")
    if args.scrip and not re.fullmatch(r"\d{6}", args.scrip):
        common.fail("india_filings.py: --scrip must be a 6-digit BSE code")

    common.emit(run(args.name, args.scrip, args.cin, args.months, args.limit, args.cache_dir), args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
