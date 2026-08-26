"""Pull filings and XBRL facts for a US filer from SEC EDGAR.

    python scripts/edgar.py --cik 0001652044 --items 1,1A --forms 10-K --limit 2
    python scripts/edgar.py --ticker INFY --facts --limit 1
    python scripts/edgar.py --cik 320193 --forms 8-K --limit 8 --no-items

Item 1 (Business) and Item 1A (Risk Factors) carry the most signal in the whole tool:
Item 1A is the company enumerating its own problems under legal obligation, which no
press release will ever do.

Item text is truncated to --max-chars per item by default. A 10-K risk section runs to
80k characters; the agent needs the first few thousand words of each, not the lot.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"
FILING_INDEX = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"

# The Items worth naming. Anything else is addressed by number alone.
ITEM_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "2": "Properties",
    "3": "Legal Proceedings",
    "5": "Market for Registrant's Common Equity",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements",
    "9A": "Controls and Procedures",
}

# XBRL concepts that answer "is this company growing, and does it make money".
# US filers report under us-gaap; foreign private issuers (Infosys, Wipro, HDFC) file
# 20-Fs under ifrs-full, so both vocabularies are listed for every metric.
FACT_CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractsWithCustomers",
        "Revenue",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalents",
    ],
    "employees": ["EntityNumberOfEmployees", "NumberOfEmployees"],
    "rnd": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseRecognisedAsExpense"],
}

ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-K/A", "20-F/A")
FACT_NAMESPACES = ("us-gaap", "ifrs-full", "dei")


def _date(value: str):
    from datetime import date

    return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))


def _cik10(value: str) -> str:
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        raise SystemExit("edgar.py: --cik must contain digits")
    return f"{int(digits):010d}"


def cik_for_ticker(ticker: str, cache_dir) -> str | None:
    headers = common.sec_headers()
    data = common.http_get(
        "https://www.sec.gov/files/company_tickers.json",
        ttl_seconds=common.cache_ttl("entity"),
        headers=headers,
        cache_dir=cache_dir,
    ).json()
    rows = data.values() if isinstance(data, dict) else data
    for row in rows:
        if str(row.get("ticker", "")).upper() == ticker.upper():
            return _cik10(row["cik_str"])
    return None


def submissions(cik: str, cache_dir) -> dict:
    headers = common.sec_headers()
    return common.http_get(
        SUBMISSIONS.format(cik=cik),
        ttl_seconds=common.cache_ttl("filings"),
        headers=headers,
        cache_dir=cache_dir,
    ).json()


def _rows(recent: dict) -> list[dict]:
    keys = list(recent)
    length = len(recent.get("accessionNumber", []))
    return [{k: recent[k][i] for k in keys if i < len(recent[k])} for i in range(length)]


def list_filings(cik: str, forms: list[str], limit: int, cache_dir, since: str | None = None) -> tuple[dict, list[dict]]:
    data = submissions(cik, cache_dir)
    rows = _rows(data.get("filings", {}).get("recent", {}))

    # Older filings live in separate JSON shards; only pull them if we came up short.
    wanted = {f.upper() for f in forms} if forms else None

    def matches(row: dict) -> bool:
        form = str(row.get("form", "")).upper()
        if wanted and form not in wanted:
            return False
        if since and str(row.get("filingDate", "")) < since:
            return False
        return True

    picked = [r for r in rows if matches(r)]
    if len(picked) < limit:
        for shard in data.get("filings", {}).get("files", [])[:3]:
            try:
                extra = common.http_get(
                    "https://data.sec.gov/submissions/" + shard["name"],
                    ttl_seconds=common.cache_ttl("filings"),
                    headers=common.sec_headers(),
                    cache_dir=cache_dir,
                ).json()
            except (common.SourceError, ValueError):
                break
            picked.extend(r for r in _rows(extra) if matches(r))
            if len(picked) >= limit:
                break

    picked.sort(key=lambda r: str(r.get("filingDate", "")), reverse=True)
    return data, picked[:limit]


def filing_url(cik: str, row: dict) -> str:
    accession = str(row.get("accessionNumber", "")).replace("-", "")
    document = row.get("primaryDocument") or ""
    return ARCHIVE.format(cik_int=int(cik), accession=accession, document=document)


# ------------------------------------------------------------------ item slicing


# "Item 1A." / "ITEM 1A -" / "Item 3.D" / "Item 4 A." -- filers format these every way
# imaginable, and 20-F numbering (3.D Risk Factors) differs from 10-K numbering.
_ITEM_HEADING = re.compile(
    r"(?im)^[ 	]{0,8}item[ 	]{0,3}(\d{1,2})[ 	]*\.?[ 	]*([A-Fa-f])?\b[ 	\.\:\-–—]{0,4}(.{0,90})$"
)


def _item_key(number: str, letter: str | None) -> str:
    return f"{int(number)}{(letter or '').upper()}"


def split_items(text: str) -> dict[str, dict]:
    """Slice a filing into Item sections.

    Filings repeat every Item heading in the table of contents, so a naive first-match
    slice returns three lines of TOC. Taking the longest span between consecutive
    headings is what makes this reliable across filers and decades of formatting.
    """
    hits = [
        (m.start(), _item_key(m.group(1), m.group(2)), (m.group(3) or "").strip())
        for m in _ITEM_HEADING.finditer(text)
    ]
    if not hits:
        return {}
    sections: dict[str, dict] = {}
    for idx, (start, key, heading) in enumerate(hits):
        end = hits[idx + 1][0] if idx + 1 < len(hits) else len(text)
        body = text[start:end].strip()
        if len(body) > len(sections.get(key, {}).get("text", "")):
            sections[key] = {"text": body, "heading": heading}
    return sections


def extract_items(url: str, items: list[str], cache_dir, max_chars: int) -> dict:
    resp = common.http_get(
        url, ttl_seconds=common.cache_ttl("filings"), headers=common.sec_headers(), cache_dir=cache_dir
    )
    text = common.strip_html(resp.text)
    sections = split_items(text)
    out = {}
    for item in items:
        key = re.sub(r"[^0-9A-Za-z]", "", item).upper()
        section = sections.get(key)
        fallback_from = None
        if not section and key[-1].isalpha():
            # 20-F filers often print "Item 3. Key Information" and then head the
            # subsections by name only, so 3D (Risk Factors) lives inside Item 3.
            parent = key[:-1]
            if sections.get(parent):
                section, fallback_from = sections[parent], parent
        if not section:
            out[key] = {
                "title": ITEM_TITLES.get(key, ""),
                "found": False,
                "chars": 0,
                "text": "",
                "available_items": sorted(sections, key=lambda k: (len(k), k)),
            }
            continue
        body = section["text"]
        out[key] = {
            "title": ITEM_TITLES.get(key) or section["heading"],
            "found": True,
            "chars": len(body),
            "truncated": len(body) > max_chars,
            "text": body[:max_chars],
        }
        if fallback_from:
            out[key]["served_from_item"] = fallback_from
    return out


# -------------------------------------------------------------------- xbrl facts


def company_facts(cik: str, cache_dir, years: int = 6) -> dict:
    try:
        data = common.http_get(
            FACTS.format(cik=cik),
            ttl_seconds=common.cache_ttl("filings"),
            headers=common.sec_headers(),
            cache_dir=cache_dir,
            timeout=60,
        ).json()
    except (common.SourceError, ValueError) as exc:
        return {"status": "gap", "reason": f"XBRL facts unavailable: {exc}"}

    namespaces = {ns: data.get("facts", {}).get(ns, {}) for ns in FACT_NAMESPACES}
    out: dict = {"status": "ok", "series": {}}
    for label, concepts in FACT_CONCEPTS.items():
        node, found_ns, found_concept = None, None, None
        for concept in concepts:
            for ns, facts in namespaces.items():
                if concept in facts:
                    node, found_ns, found_concept = facts[concept], ns, concept
                    break
            if node:
                break
        if not node:
            continue
        unit_key = next(iter(node.get("units", {})), None)
        if not unit_key:
            continue

        # `fy` on a fact is the fiscal year of the *filing that reported it*, not of the
        # period measured -- a 20-F restating three years tags all three fy=2018. Key on
        # the period end date instead, and keep only full-year durations.
        annual: dict[str, dict] = {}
        for point in node["units"][unit_key]:
            if point.get("form") not in ANNUAL_FORMS:
                continue
            end = point.get("end")
            if not end:
                continue
            start = point.get("start")
            if start:
                span = (_date(end) - _date(start)).days
                if not 330 <= span <= 400:
                    continue
            prior = annual.get(end)
            if prior is None or str(point.get("filed", "")) > str(prior.get("filed", "")):
                annual[end] = point
        if not annual:
            continue
        series = [
            {
                "period_end": end,
                "fiscal_year": int(end[:4]),
                "value": annual[end].get("val"),
                "unit": unit_key,
                "form": annual[end].get("form"),
                "accession": annual[end].get("accn"),
            }
            for end in sorted(annual)[-years:]
        ]
        out["series"][label] = {"concept": found_concept, "namespace": found_ns, "points": series}

    revenue = out["series"].get("revenue", {}).get("points", [])
    if len(revenue) >= 2:
        first, last, recent = revenue[0]["value"], revenue[-1]["value"], revenue[-2]["value"]
        cagr = ((last / first) ** (1 / max(len(revenue) - 1, 1)) - 1) if first else None
        yoy = ((last - recent) / recent) if recent else None
        trend = "flat"
        if yoy is not None:
            trend = "growing" if yoy > 0.05 else "declining" if yoy < -0.05 else "flat"
        out["revenue_trend"] = {
            "value": trend,
            "yoy_change": round(yoy, 4) if yoy is not None else None,
            "cagr": round(cagr, 4) if cagr is not None else None,
            "period_ends": [p["period_end"] for p in revenue],
            "note": "signal revenue_trend takes this value; confidence high when 3+ years",
        }
    return out


# --------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cik", help="10-digit CIK (leading zeros optional)")
    parser.add_argument("--ticker", help="resolve the CIK from a ticker instead")
    parser.add_argument("--forms", default="10-K", help="comma-separated form types (default 10-K)")
    parser.add_argument("--items", default="1,1A", help="comma-separated Item sections to extract")
    parser.add_argument("--no-items", action="store_true", help="list filings only, do not fetch documents")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--since", help="only filings on or after this date (YYYY-MM-DD)")
    parser.add_argument("--max-chars", type=int, default=20000, help="per-item truncation (default 20000)")
    parser.add_argument("--facts", action="store_true", help="include XBRL revenue/income/cash series")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    try:
        if args.ticker and not args.cik:
            cik = cik_for_ticker(args.ticker, args.cache_dir)
            if not cik:
                common.fail(f"edgar.py: no CIK found for ticker {args.ticker}")
        else:
            if not args.cik:
                common.fail("edgar.py: pass --cik or --ticker")
            cik = _cik10(args.cik)

        forms = [f.strip() for f in args.forms.split(",") if f.strip()]
        items = [i.strip() for i in args.items.split(",") if i.strip()]
        data, rows = list_filings(cik, forms, args.limit, args.cache_dir, args.since)
    except common.MissingContactEmail as exc:
        common.fail(str(exc), 2)
    except common.SourceError as exc:
        common.fail(f"edgar.py: {exc}")

    filings = []
    for row in rows:
        url = filing_url(cik, row)
        record = {
            "form": row.get("form"),
            "filing_date": row.get("filingDate"),
            "report_date": row.get("reportDate"),
            "accession": row.get("accessionNumber"),
            "description": row.get("primaryDocDescription") or row.get("items"),
            "url": url,
        }
        if not args.no_items and items:
            try:
                record["items"] = extract_items(url, items, args.cache_dir, args.max_chars)
            except common.SourceError as exc:
                record["items"] = {}
                record["error"] = str(exc)  # one dead document must not kill the run
        filings.append(record)

    payload = {
        "cik": cik,
        "entity_name": data.get("name"),
        "tickers": data.get("tickers", []),
        "exchanges": data.get("exchanges", []),
        "sic_description": data.get("sicDescription"),
        "fiscal_year_end": data.get("fiscalYearEnd"),
        "state_of_incorporation": data.get("stateOfIncorporation"),
        "filings": filings,
        "retrieved_at": common.iso_now(),
    }
    if args.facts:
        payload["facts"] = company_facts(cik, args.cache_dir)
    common.emit(payload, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
