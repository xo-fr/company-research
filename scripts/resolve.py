"""Resolve a brand name (or a job posting) to a legal entity tree.

    python scripts/resolve.py --name "Zomato" --market IN
    python scripts/resolve.py --jd-url https://boards.greenhouse.io/acme/jobs/123
    python scripts/resolve.py --name "Acme" --domain acme.com --jd-out /tmp/jd.txt

Emits the ``entity`` object of the evidence schema on stdout. When confidence is below
0.7 it emits ``candidates`` instead of committing to one entity -- the skill then asks
the user. Guessing between two plausible entities is worse than asking: every downstream
pillar inherits the mistake.

Resolution order (BUILD-SPEC section 7):
  1. job posting  -> canonical domain, footer legal name, role, location
  2. Wikidata     -> QID, aliases, official site, tickers, subsidiaries, country
  3. US           -> SEC company_tickers.json -> CIK
  4. India        -> CIN from Wikidata claims, BSE scrip code for listed entities
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

WD_API = "https://www.wikidata.org/w/api.php"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
BSE_SCRIP_MASTER = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    "?Group=&Scripcode=&industry=&segment=Equity&status=Active"
)
BSE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
}

CIN_RE = re.compile(r"\b[LUu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}\b")
LEGAL_TAIL_RE = re.compile(
    r"(?:[A-Z][\w&.,'-]*\s+){0,5}[A-Z][\w&.,'-]*\s+"
    r"(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|Limited|Ltd\.?|Inc\.?|Incorporated|"
    r"Corporation|Corp\.?|LLC|LLP|GmbH|B\.?V\.?|PLC)",
)

# Wikidata properties worth reading. Anything else is noise for this tool.
P_OFFICIAL_SITE = "P856"
P_COUNTRY = "P17"
P_TICKER = "P414"          # stock exchange (qualifier P249 carries the symbol)
P_SUBSIDIARY = "P355"
P_PARENT = "P749"
P_INDUSTRY = "P452"
P_INCEPTION = "P571"
P_EMPLOYEES = "P1128"
P_LEGAL_FORM = "P1454"
P_CIK = "P5531"
P_ISIN = "P946"

GCC_HINTS = ("india development cent", "global capability", "gcc", "technology cent")
VENDOR_HINTS = (
    "staffing", "consultanc", "recruit", "manpower", "outsourc", "talent solutions",
    "infotech services", "it services",
)


# ------------------------------------------------------------------ job posting


def read_jd(url: str, cache_dir) -> dict:
    """Pull domain, legal name, role and location out of a job posting."""
    out: dict = {"jd_url": url, "domain": None, "legal_names": [], "role": None, "location": None}
    try:
        resp = common.http_get(url, ttl_seconds=common.cache_ttl("default"), cache_dir=cache_dir)
    except common.SourceError as exc:
        out["error"] = str(exc)
        return out
    html = resp.text
    text = common.strip_html(html)
    out["text"] = text

    canonical = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html, re.I)
    candidates = [canonical.group(1)] if canonical else []
    og = re.search(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if og:
        candidates.append(og.group(1))
    candidates.append(url)
    for cand in candidates:
        host = urllib.parse.urlsplit(cand).netloc.lower().lstrip("www.")
        if host and not _is_ats_host(host):
            out["domain"] = host
            break
    out["ats_host"] = _is_ats_host(urllib.parse.urlsplit(url).netloc.lower())

    # Careers pages on ATS hosts still name the employer in the title or the footer.
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if title:
        out["page_title"] = common.strip_html(title.group(1)).strip()
    for match in LEGAL_TAIL_RE.finditer(text):
        name = " ".join(match.group(0).split())
        if name not in out["legal_names"]:
            out["legal_names"].append(name)
    out["legal_names"] = out["legal_names"][:5]

    cin = CIN_RE.search(text)
    if cin:
        out["cin"] = cin.group(0).upper()

    heading = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if heading:
        out["role"] = common.strip_html(heading.group(1)).strip()[:120]
    elif out.get("page_title"):
        out["role"] = out["page_title"].split(" - ")[0][:120]

    loc = re.search(
        r"(?im)^(?:location|office|based in)\s*[:\-]\s*(.{3,60})$", text
    ) or re.search(r"\b(Bengaluru|Bangalore|Hyderabad|Pune|Chennai|Mumbai|Gurgaon|Gurugram|Noida|"
                   r"Delhi|San Francisco|New York|Seattle|Austin|Boston|Remote)\b", text)
    if loc:
        out["location"] = loc.group(1).strip() if loc.lastindex else loc.group(0)
    return out


_ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
    "myworkdayjobs.com", "workday.com", "taleo.net", "icims.com", "jobvite.com",
    "bamboohr.com", "recruitee.com", "teamtailor.com", "linkedin.com", "indeed.com",
    "naukri.com", "glassdoor.com", "wellfound.com", "angel.co", "hire.lever.co",
)


def _is_ats_host(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in _ATS_HOSTS)


# --------------------------------------------------------------------- wikidata


def wd_search(term: str, cache_dir, limit: int = 7) -> list[dict]:
    url = (
        f"{WD_API}?action=wbsearchentities&format=json&language=en&uselang=en"
        f"&type=item&limit={limit}&search={urllib.parse.quote(term)}"
    )
    try:
        data = common.http_get(url, ttl_seconds=common.cache_ttl("entity"), cache_dir=cache_dir).json()
    except (common.SourceError, ValueError):
        return []
    return data.get("search", [])


def wd_entities(qids: list[str], cache_dir) -> dict:
    if not qids:
        return {}
    url = (
        f"{WD_API}?action=wbgetentities&format=json&languages=en"
        f"&props=labels|aliases|claims|sitelinks&ids={'|'.join(qids[:20])}"
    )
    try:
        data = common.http_get(url, ttl_seconds=common.cache_ttl("entity"), cache_dir=cache_dir).json()
    except (common.SourceError, ValueError):
        return {}
    return data.get("entities", {})


def _claim_values(entity: dict, prop: str) -> list:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        value = snak.get("datavalue", {}).get("value")
        if value is None:
            continue
        qualifiers = claim.get("qualifiers", {})
        out.append({"value": value, "qualifiers": qualifiers})
    return out


def _plain(value):
    if isinstance(value, dict):
        return value.get("id") or value.get("text") or value.get("time") or value.get("amount")
    return value


def wd_profile(qid: str, entity: dict) -> dict:
    """Flatten the handful of Wikidata claims this tool actually uses."""
    labels = entity.get("labels", {}).get("en", {}).get("value", "")
    aliases = [a["value"] for a in entity.get("aliases", {}).get("en", [])]
    site = [_plain(v["value"]) for v in _claim_values(entity, P_OFFICIAL_SITE)]
    tickers = []
    for claim in _claim_values(entity, P_TICKER):
        exchange = _plain(claim["value"])
        symbols = [
            q.get("datavalue", {}).get("value")
            for q in claim["qualifiers"].get("P249", [])
        ]
        for sym in symbols:
            if sym:
                tickers.append({"exchange_qid": exchange, "symbol": sym})
    cin = None
    for prop, claims in entity.get("claims", {}).items():
        for claim in claims:
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(value, str) and CIN_RE.fullmatch(value.strip()):
                cin = value.strip().upper()
    return {
        "qid": qid,
        "label": labels,
        "aliases": aliases,
        "official_site": site[0] if site else None,
        "country_qids": [_plain(v["value"]) for v in _claim_values(entity, P_COUNTRY)],
        "tickers": tickers,
        "cik": next((_plain(v["value"]) for v in _claim_values(entity, P_CIK)), None),
        "isin": next((_plain(v["value"]) for v in _claim_values(entity, P_ISIN)), None),
        "cin": cin,
        "subsidiary_qids": [_plain(v["value"]) for v in _claim_values(entity, P_SUBSIDIARY)][:25],
        "parent_qids": [_plain(v["value"]) for v in _claim_values(entity, P_PARENT)][:5],
        "industry_qids": [_plain(v["value"]) for v in _claim_values(entity, P_INDUSTRY)][:5],
        "inception": next((_plain(v["value"]) for v in _claim_values(entity, P_INCEPTION)), None),
        "employees": next((_plain(v["value"]) for v in _claim_values(entity, P_EMPLOYEES)), None),
        "legal_form_qids": [_plain(v["value"]) for v in _claim_values(entity, P_LEGAL_FORM)][:3],
    }


# -------------------------------------------------------------------------- SEC


def sec_ticker_map(cache_dir) -> list[dict]:
    try:
        headers = common.sec_headers()
    except common.MissingContactEmail:
        return []
    try:
        data = common.http_get(
            SEC_TICKERS, ttl_seconds=common.cache_ttl("entity"), headers=headers, cache_dir=cache_dir
        ).json()
    except (common.SourceError, ValueError):
        return []
    rows = data.values() if isinstance(data, dict) else data
    return [
        {
            "cik": f"{int(r['cik_str']):010d}",
            "ticker": r.get("ticker", ""),
            "title": r.get("title", ""),
        }
        for r in rows
        if r.get("cik_str")
    ]


def sec_match(name: str, ticker: str | None, cache_dir) -> list[dict]:
    rows = sec_ticker_map(cache_dir)
    if not rows:
        return []
    if ticker:
        exact = [r for r in rows if r["ticker"].upper() == ticker.upper()]
        if exact:
            return exact
    target = common.normalize_company_name(name)
    if not target:
        return []
    exact = [r for r in rows if common.normalize_company_name(r["title"]) == target]
    if exact:
        return exact
    return [
        r for r in rows
        if target and target in common.normalize_company_name(r["title"])
    ][:5]


# ------------------------------------------------------------------------ India


def bse_master(cache_dir) -> list[dict]:
    """Every active BSE equity scrip: name, code, ISIN, issuer. ~1.7MB, cached 90 days."""
    try:
        data = common.http_get(
            BSE_SCRIP_MASTER,
            ttl_seconds=common.cache_ttl("entity"),
            headers=BSE_HEADERS,
            cache_dir=cache_dir,
            timeout=60,
        ).json()
    except (common.SourceError, ValueError):
        return []
    return data if isinstance(data, list) else []


def bse_scrip(name: str, cache_dir, isin: str | None = None) -> dict | None:
    """Look a listed Indian company up in the BSE master. Unlisted ones are a known gap.

    ISIN wins when Wikidata supplied one: it is an exact identifier, where names are not
    ("Infosys Ltd" vs "Infosys Limited" vs "INFOSYS LTD.").
    """
    rows = bse_master(cache_dir)
    if not rows:
        return None
    if isin:
        for row in rows:
            if (row.get("ISIN_NUMBER") or "").strip().upper() == isin.strip().upper():
                return _bse_row(row)
    target = common.normalize_company_name(name)
    if not target:
        return None
    exact = [
        r for r in rows
        if common.normalize_company_name(r.get("Scrip_Name", "")) == target
        or common.normalize_company_name(r.get("Issuer_Name", "")) == target
    ]
    if exact:
        return _bse_row(exact[0])
    partial = [
        r for r in rows
        if target in common.normalize_company_name(r.get("Scrip_Name", ""))
    ]
    return _bse_row(partial[0]) if len(partial) == 1 else None


def _bse_row(row: dict) -> dict:
    return {
        "legal_name": (row.get("Issuer_Name") or row.get("Scrip_Name") or "").strip(),
        "scrip_code": str(row.get("SCRIP_CD", "")).strip(),
        "isin": (row.get("ISIN_NUMBER") or "").strip() or None,
        "scrip_id": (row.get("scrip_id") or "").strip() or None,
    }


# ------------------------------------------------------------------ orchestration


def _score(candidate: dict, name: str, domain: str | None) -> float:
    score = 0.0
    target = common.normalize_company_name(name)
    label = common.normalize_company_name(candidate.get("label", ""))
    aliases = {common.normalize_company_name(a) for a in candidate.get("aliases", [])}
    if label == target:
        score += 0.45
    elif target and (target in label or label in target or target in aliases):
        score += 0.3
    if domain and candidate.get("official_site"):
        site_host = urllib.parse.urlsplit(candidate["official_site"]).netloc.lower().lstrip("www.")
        if site_host and (site_host == domain or site_host.endswith("." + domain) or domain.endswith("." + site_host)):
            score += 0.35
    if candidate.get("cik") or candidate.get("tickers"):
        score += 0.15
    if candidate.get("cin") or candidate.get("isin"):
        score += 0.1
    desc = (candidate.get("description") or "").lower()
    if any(w in desc for w in ("company", "business", "enterprise", "corporation", "startup")):
        score += 0.05
    return min(score, 1.0)


def _employment_type(market: str, entities: list[dict], profile: dict, jd: dict) -> tuple[str, str]:
    names = " ".join(e.get("legal_name", "").lower() for e in entities)
    jd_names = " ".join(jd.get("legal_names", [])).lower()
    blob = f"{names} {jd_names}"
    if any(h in blob for h in VENDOR_HINTS):
        return "vendor", "legal name matches a staffing/services pattern"
    if market == "IN":
        in_entity = next((e for e in entities if e.get("jurisdiction") == "IN"), None)
        parents = profile.get("parent_qids") or []
        foreign_parent = bool(parents) or any(
            c not in ("Q668",) for c in (profile.get("country_qids") or [])
        )
        if in_entity and foreign_parent:
            return "gcc", "Indian subsidiary of a foreign-domiciled parent"
        if any(h in blob for h in GCC_HINTS):
            return "gcc", "entity name matches a capability-centre pattern"
        if in_entity:
            return "direct", "Indian entity with no foreign parent found"
    if entities:
        return "direct", "single-jurisdiction entity"
    return "unknown", "no legal entity resolved"


def resolve(
    name: str | None,
    domain: str | None,
    market: str | None,
    jd_url: str | None,
    cache_dir,
    jd_out: str | None = None,
) -> dict:
    jd: dict = {}
    if jd_url:
        jd = read_jd(jd_url, cache_dir)
        domain = domain or jd.get("domain")
        if not name:
            name = (jd.get("legal_names") or [None])[0] or (jd.get("page_title") or "").split("|")[-1].strip()
        if jd_out and jd.get("text"):
            Path(jd_out).expanduser().parent.mkdir(parents=True, exist_ok=True)
            Path(jd_out).expanduser().write_text(jd["text"], encoding="utf-8")

    if not name and domain:
        name = domain.split(".")[0]
    if not name:
        raise SystemExit("resolve.py: need --name, --domain or --jd-url")

    market = (market or "").upper() or None

    # --- Wikidata candidates ------------------------------------------------
    terms = [name]
    if domain:
        terms.append(domain.split(".")[0])
    seen: dict[str, dict] = {}
    for term in terms:
        for hit in wd_search(term, cache_dir):
            seen.setdefault(hit["id"], hit)
    entities = wd_entities(list(seen), cache_dir)
    candidates = []
    for qid, hit in seen.items():
        ent = entities.get(qid)
        if not ent:
            continue
        prof = wd_profile(qid, ent)
        prof["description"] = hit.get("description", "")
        prof["score"] = _score(prof, name, domain)
        candidates.append(prof)
    candidates.sort(key=lambda c: c["score"], reverse=True)

    best = candidates[0] if candidates else None
    runner_up = candidates[1]["score"] if len(candidates) > 1 else 0.0
    confidence = 0.0
    if best:
        confidence = best["score"]
        if best["score"] - runner_up < 0.1:  # two equally plausible entities
            confidence *= 0.75

    # --- jurisdiction-specific identifiers ----------------------------------
    tree_entities: list[dict] = []
    notes: list[str] = []

    ticker_symbol = None
    if best and best.get("tickers"):
        ticker_symbol = best["tickers"][0]["symbol"]

    sec_rows = sec_match(best["label"] if best else name, ticker_symbol, cache_dir)
    if best and best.get("cik"):
        cik = f"{int(str(best['cik']).lstrip('CIK')):010d}"
        tree_entities.append(
            {
                "id": f"us:{cik}",
                "jurisdiction": "US",
                "legal_name": best["label"],
                "cik": cik,
                "ticker": ticker_symbol,
                "source": "wikidata:P5531",
            }
        )
    elif sec_rows:
        if len(sec_rows) > 1 and not ticker_symbol:
            notes.append(f"{len(sec_rows)} SEC filers match that name; picked none")
        else:
            row = sec_rows[0]
            tree_entities.append(
                {
                    "id": f"us:{row['cik']}",
                    "jurisdiction": "US",
                    "legal_name": row["title"],
                    "cik": row["cik"],
                    "ticker": row["ticker"],
                    "source": "sec:company_tickers.json",
                }
            )

    if market == "IN" or (best and "Q668" in (best.get("country_qids") or [])):
        cin = jd.get("cin") or (best.get("cin") if best else None)
        india_name = None
        scrip = bse_scrip(best["label"] if best else name, cache_dir, best.get("isin") if best else None)
        if scrip:
            india_name = scrip["legal_name"]
        for candidate_name in jd.get("legal_names", []):
            if re.search(r"(?i)(private\s+limited|pvt\.?\s*ltd|limited|ltd)\b", candidate_name):
                india_name = india_name or candidate_name
        if cin or india_name or scrip:
            tree_entities.append(
                {
                    "id": f"in:{cin}" if cin else f"in:{common.normalize_company_name(india_name or name).replace(' ', '-')}",
                    "jurisdiction": "IN",
                    "legal_name": india_name or (best["label"] if best else name),
                    "cin": cin,
                    "bse_scrip_code": scrip["scrip_code"] if scrip else None,
                    "isin": (scrip.get("isin") if scrip else None) or (best.get("isin") if best else None),
                    "source": "bse" if scrip else ("wikidata" if cin else "job-posting"),
                }
            )
        else:
            notes.append(
                "no Indian registry identifier found: MCA has no free API and this "
                "entity is not BSE-listed. Unlisted Indian subsidiaries are a known gap."
            )

    employment_type, employment_reason = _employment_type(
        market or "", tree_entities, best or {}, jd
    )

    resolved_id = None
    if tree_entities:
        preferred = "IN" if market == "IN" else "US"
        pick = next((e for e in tree_entities if e["jurisdiction"] == preferred), tree_entities[0])
        resolved_id = pick["id"]

    entity = {
        "brand": best["label"] if best else name,
        "domain": domain or (
            urllib.parse.urlsplit(best["official_site"]).netloc.lstrip("www.")
            if best and best.get("official_site") else None
        ),
        "resolved_entity_id": resolved_id,
        "employment_type": employment_type,
        "employment_type_reason": employment_reason,
        "confidence": round(confidence, 2),
        "tree": {
            "brand": {
                "wikidata_qid": best["qid"] if best else None,
                "aliases": ([best["label"]] + best["aliases"])[:12] if best else [],
            },
            "entities": tree_entities,
        },
        "notes": notes,
    }
    if best:
        entity["wikidata"] = {
            k: best[k]
            for k in ("official_site", "industry_qids", "inception", "employees",
                      "parent_qids", "subsidiary_qids", "country_qids", "isin")
        }
    if jd:
        entity["jd"] = {
            k: jd.get(k) for k in ("jd_url", "role", "location", "ats_host", "page_title", "legal_names")
        }
        if jd_out:
            entity["jd"]["jd_text_path"] = str(Path(jd_out).expanduser())

    if confidence < 0.7 or not resolved_id:
        entity["candidates"] = [
            {
                "wikidata_qid": c["qid"],
                "label": c["label"],
                "description": c.get("description", ""),
                "official_site": c.get("official_site"),
                "score": round(c["score"], 2),
            }
            for c in candidates[:5]
        ]
        entity.setdefault("notes", []).append(
            "confidence below 0.7: ask the user which entity is meant before researching"
            if confidence < 0.7 else
            "no registry identifier resolved; pillars must bind to the brand, not an entity id"
        )
    return entity


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", help="brand or legal name")
    parser.add_argument("--domain", help="company domain, e.g. acme.com")
    parser.add_argument("--market", help="IN or US")
    parser.add_argument("--jd-url", help="job posting URL")
    parser.add_argument("--jd-out", help="write extracted JD text to this path")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    entity = resolve(args.name, args.domain, args.market, args.jd_url, args.cache_dir, args.jd_out)
    common.emit(entity, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
