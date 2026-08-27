"""US pay and visa-sponsorship history from DOL LCA disclosure data.

    python scripts/h1b.py --employer "STRIPE, INC." --year 2025
    python scripts/h1b.py --employer "GOOGLE LLC" --title "SOFTWARE ENGINEER" --state CA
    python scripts/h1b.py --list-files

Every H-1B, E-3 and H-1B1 petition requires a Labor Condition Application naming the
employer, the job title, the worksite and **the wage**. DOL publishes the lot quarterly.
It is the only free, structured, employer-specific pay source that exists for the US
market, and it is why US compensation is answerable here while India's is not.

The quarterly file is 80-250 MB of xlsx. It is downloaded once into
``~/.company-research/cache/h1b/`` with progress on stderr, then parsed by streaming --
never loaded whole. Subsequent runs for the same year read the cached copy.

Wages are reported as filed. LCA wages are the *minimum* the employer commits to pay,
so treat them as a floor for base salary, never as total compensation.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

PERFORMANCE_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Columns worth reading. DOL renames these between years, hence the alternatives.
COLUMNS = {
    "employer": ["EMPLOYER_NAME", "EMPLOYER_NAME_LEGAL", "LEGAL_BUSINESS_NAME"],
    "job_title": ["JOB_TITLE"],
    "soc_title": ["SOC_TITLE", "SOC_NAME"],
    "status": ["CASE_STATUS"],
    "decision_date": ["DECISION_DATE"],
    "begin_date": ["BEGIN_DATE", "EMPLOYMENT_START_DATE"],
    "wage_from": ["WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_FROM_1"],
    "wage_to": ["WAGE_RATE_OF_PAY_TO", "WAGE_RATE_OF_PAY_TO_1"],
    "wage_unit": ["WAGE_UNIT_OF_PAY", "WAGE_UNIT_OF_PAY_1", "PW_UNIT_OF_PAY"],
    "state": ["WORKSITE_STATE", "WORKSITE_STATE_1", "EMPLOYER_STATE"],
    "city": ["WORKSITE_CITY", "WORKSITE_CITY_1"],
    "full_time": ["FULL_TIME_POSITION"],
    "visa_class": ["VISA_CLASS"],
}

UNIT_TO_ANNUAL = {
    "year": 1.0, "yr": 1.0, "annual": 1.0,
    "hour": 2080.0, "hr": 2080.0,
    "week": 52.0, "wk": 52.0,
    "bi-weekly": 26.0, "biweekly": 26.0,
    "month": 12.0, "mth": 12.0, "monthly": 12.0,
}


# ------------------------------------------------------------------ file discovery


def disclosure_files(cache_dir) -> list[dict]:
    """Read the current download URLs off the DOL page rather than hardcoding them.

    DOL moves these files between /sites/dolgov/files/... and /media/... without notice,
    so a hardcoded URL is a guaranteed future outage.
    """
    resp = common.http_get(
        PERFORMANCE_PAGE, ttl_seconds=common.cache_ttl("compensation"), cache_dir=cache_dir, timeout=60
    )
    out = []
    for href in re.findall(r'href="([^"]+\.xlsx)"', resp.text):
        name = href.rsplit("/", 1)[-1]
        match = re.match(r"(?i)LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx", name)
        if not match:
            continue
        url = href if href.startswith("http") else "https://www.dol.gov" + href
        scheme, _, rest = url.partition("://")
        url = scheme + "://" + re.sub(r"/{2,}", "/", rest)  # DOL emits //media/... links
        out.append(
            {"fiscal_year": int(match.group(1)), "quarter": int(match.group(2)), "url": url, "name": name}
        )
    out.sort(key=lambda r: (r["fiscal_year"], r["quarter"]))
    return out


def pick_file(files: list[dict], year: int | None) -> dict | None:
    """Q4 is the whole fiscal year; earlier quarters are cumulative to that point."""
    if not files:
        return None
    if year:
        for_year = [f for f in files if f["fiscal_year"] == year]
        return max(for_year, key=lambda f: f["quarter"]) if for_year else None
    return files[-1]


def download(url: str, dest: Path, quiet: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": common.user_agent()})
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=120) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if not quiet and total:
                pct = done * 100 // total
                print(
                    f"\r  downloading {dest.name}: {done/1e6:6.1f} / {total/1e6:.1f} MB ({pct}%)",
                    end="", file=sys.stderr, flush=True,
                )
    if not quiet:
        print("", file=sys.stderr)
    tmp.replace(dest)
    return dest


# --------------------------------------------------------------------- xlsx reader


def _sheet_path(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
    if not names:
        raise common.SourceError("unexpected xlsx layout: no worksheet found")
    return sorted(names)[0]


def _shared_strings(zf: zipfile.ZipFile, wanted) -> tuple[Path, list[int], set[int]]:
    """Stream the shared-string table to disk and index it.

    The table in a DOL quarter runs to millions of entries; holding it in memory is how
    a helper script turns into a 2 GB process. Instead each string is appended to a temp
    file, its offset recorded, and the indices matching ``wanted`` collected on the way
    past -- which lets the row scan filter on an integer compare instead of a lookup.
    """
    handle = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".strings")
    offsets: list[int] = []
    matches: set[int] = set()
    position = 0
    if "xl/sharedStrings.xml" not in zf.namelist():
        handle.close()
        return Path(handle.name), offsets, matches

    with zf.open("xl/sharedStrings.xml") as stream:
        buffer = []
        index = 0
        for event, element in ET.iterparse(stream, events=("end",)):
            if element.tag != NS + "si":
                continue
            text = "".join(element.itertext())
            encoded = text.replace("\n", " ").encode("utf-8", "replace") + b"\n"
            buffer.append(encoded)
            offsets.append(position)
            position += len(encoded)
            if wanted and wanted(text):
                matches.add(index)
            index += 1
            element.clear()
            if len(buffer) >= 20000:
                handle.write(b"".join(buffer))
                buffer = []
        if buffer:
            handle.write(b"".join(buffer))
    handle.close()
    return Path(handle.name), offsets, matches


class StringTable:
    def __init__(self, path: Path, offsets: list[int]) -> None:
        self.path = path
        self.offsets = offsets
        self.fh = open(path, "rb")

    def get(self, index: int) -> str:
        if index < 0 or index >= len(self.offsets):
            return ""
        self.fh.seek(self.offsets[index])
        return self.fh.readline().decode("utf-8", "replace").rstrip("\n")

    def close(self) -> None:
        self.fh.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _col_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return -1
    value = 0
    for ch in letters.group(1):
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def _cell_value(cell, table: StringTable) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(NS + "is")
        return "".join(node.itertext()) if node is not None else ""
    value = cell.find(NS + "v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        return table.get(int(value.text))
    return value.text


def _excel_date(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    try:  # Excel serial, 1900 date system (with its famous phantom 29 Feb 1900)
        serial = float(value)
    except ValueError:
        return None
    return (date(1899, 12, 30) + timedelta(days=int(serial))).isoformat()


def scan(
    xlsx: Path,
    employer_pred,
    title_filter: str | None,
    state: str | None,
    limit_rows: int | None = None,
    progress: bool = True,
) -> tuple[list[dict], dict]:
    """Stream one quarterly file, returning the rows for a matching employer."""
    rows: list[dict] = []
    meta = {"rows_scanned": 0, "employers_matched": set()}
    with zipfile.ZipFile(xlsx) as zf:
        strings_path, offsets, matched_ids = _shared_strings(zf, employer_pred)
        table = StringTable(strings_path, offsets)
        try:
            sheet = _sheet_path(zf)
            header: dict[int, str] = {}
            column_of: dict[str, int] = {}
            with zf.open(sheet) as stream:
                for event, element in ET.iterparse(stream, events=("end",)):
                    if element.tag != NS + "row":
                        continue
                    cells = element.findall(NS + "c")
                    if not header:
                        for cell in cells:
                            header[_col_index(cell.get("r", ""))] = _cell_value(cell, table).strip().upper()
                        for key, aliases in COLUMNS.items():
                            for idx, name in header.items():
                                if name in aliases:
                                    column_of[key] = idx
                                    break
                        element.clear()
                        continue

                    meta["rows_scanned"] += 1
                    if progress and meta["rows_scanned"] % 100000 == 0:
                        print(f"\r  scanned {meta['rows_scanned']:,} rows", end="", file=sys.stderr, flush=True)

                    employer_col = column_of.get("employer", -1)
                    hit = False
                    values: dict[int, object] = {}
                    for cell in cells:
                        idx = _col_index(cell.get("r", ""))
                        values[idx] = cell
                        if idx == employer_col and cell.get("t") == "s":
                            node = cell.find(NS + "v")
                            if node is not None and node.text and int(node.text) in matched_ids:
                                hit = True
                    if not hit:
                        element.clear()
                        continue

                    record = {}
                    for key, idx in column_of.items():
                        cell = values.get(idx)
                        record[key] = _cell_value(cell, table).strip() if cell is not None else ""
                    element.clear()

                    if title_filter and title_filter.lower() not in (record.get("job_title", "") + " " + record.get("soc_title", "")).lower():
                        continue
                    if state and record.get("state", "").upper() != state.upper():
                        continue
                    record["decision_date"] = _excel_date(record.get("decision_date", ""))
                    record["begin_date"] = _excel_date(record.get("begin_date", ""))
                    record["annual_wage"] = _annualise(record.get("wage_from"), record.get("wage_unit"))
                    meta["employers_matched"].add(record.get("employer", ""))
                    rows.append(record)
                    if limit_rows and len(rows) >= limit_rows:
                        break
        finally:
            table.close()
    if progress:
        print("", file=sys.stderr)
    meta["employers_matched"] = sorted(meta["employers_matched"])
    return rows, meta


def _annualise(wage: str | None, unit: str | None) -> float | None:
    try:
        value = float(str(wage).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    factor = UNIT_TO_ANNUAL.get((unit or "year").strip().lower())
    if not factor or value <= 0:
        return None
    annual = value * factor
    return round(annual, 2) if 1000 <= annual <= 10_000_000 else None


# ------------------------------------------------------------------------ analysis


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 2)


def summarise(rows: list[dict], top_titles: int = 12) -> dict:
    wages = [r["annual_wage"] for r in rows if r.get("annual_wage")]
    by_title: dict[str, list[float]] = {}
    by_state: dict[str, int] = {}
    by_year: dict[str, int] = {}
    certified = 0
    for row in rows:
        title = (row.get("job_title") or row.get("soc_title") or "unknown").strip()
        by_title.setdefault(title, [])
        if row.get("annual_wage"):
            by_title[title].append(row["annual_wage"])
        st = (row.get("state") or "").upper()
        if st:
            by_state[st] = by_state.get(st, 0) + 1
        year = (row.get("decision_date") or "")[:4]
        if year:
            by_year[year] = by_year.get(year, 0) + 1
        if (row.get("status") or "").upper().startswith("CERTIFIED"):
            certified += 1
    titles = sorted(by_title.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_titles]
    return {
        "filings": len(rows),
        "certified": certified,
        "wage_percentiles": {
            "p25": percentile(wages, 0.25),
            "p50": percentile(wages, 0.50),
            "p75": percentile(wages, 0.75),
            "p90": percentile(wages, 0.90),
        },
        "wage_sample_size": len(wages),
        "by_title": [
            {
                "title": title,
                "filings": len(values),
                "median_annual_wage": percentile(values, 0.5),
                "p25": percentile(values, 0.25),
                "p75": percentile(values, 0.75),
            }
            for title, values in titles
        ],
        "by_state": dict(sorted(by_state.items(), key=lambda kv: kv[1], reverse=True)[:12]),
        "by_decision_year": dict(sorted(by_year.items())),
    }


def employer_predicate(employer: str):
    target = common.normalize_company_name(employer)
    tokens = [t for t in target.split() if len(t) > 2]

    def matches(text: str) -> bool:
        if not text or len(text) > 200:
            return False
        candidate = common.normalize_company_name(text)
        if not candidate:
            return False
        if candidate == target:
            return True
        # "GOOGLE LLC" vs "GOOGLE PAYMENT CORP": require the full target as a prefix,
        # so "Meta" does not swallow "Metabase".
        return bool(tokens) and (candidate.startswith(target + " ") or candidate == target)

    return matches


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--employer", help="employer legal name as filed, e.g. 'STRIPE, INC.'")
    parser.add_argument("--title", help="filter to job titles containing this string")
    parser.add_argument("--state", help="two-letter worksite state")
    parser.add_argument("--year", type=int, help="fiscal year (default: newest published)")
    parser.add_argument("--years", help="comma-separated fiscal years, e.g. 2023,2024,2025")
    parser.add_argument("--list-files", action="store_true", help="show available quarterly files and exit")
    parser.add_argument("--max-rows", type=int, help="stop after this many matching rows")
    parser.add_argument("--quiet", action="store_true", help="no progress output on stderr")
    parser.add_argument("--csv-out", help="also write the matching rows to this CSV")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    try:
        files = disclosure_files(args.cache_dir)
    except common.SourceError as exc:
        common.fail(f"h1b.py: cannot read the DOL disclosure index: {exc}")

    if args.list_files:
        common.emit({"files": files, "source": PERFORMANCE_PAGE}, args.pretty)
        return 0

    if not args.employer:
        common.fail("h1b.py: pass --employer (or --list-files)")

    years = [int(y) for y in args.years.split(",")] if args.years else [args.year] if args.year else [None]
    cache_root = Path(args.cache_dir).expanduser() / "h1b"

    per_year, all_rows, used = [], [], []
    for year in years:
        chosen = pick_file(files, year)
        if not chosen:
            per_year.append({"fiscal_year": year, "status": "gap", "reason": "no published file for that fiscal year"})
            continue
        path = cache_root / chosen["name"]
        if not args.quiet and not path.exists():
            print(f"  first run for FY{chosen['fiscal_year']}: fetching the quarterly disclosure file", file=sys.stderr)
        try:
            download(chosen["url"], path, quiet=args.quiet)
        except Exception as exc:
            per_year.append({"fiscal_year": chosen["fiscal_year"], "status": "gap", "reason": f"download failed: {exc}"})
            continue
        rows, meta = scan(
            path, employer_predicate(args.employer), args.title, args.state,
            args.max_rows, progress=not args.quiet,
        )
        all_rows.extend(rows)
        used.append({**chosen, "rows_scanned": meta["rows_scanned"], "cached_at": str(path)})
        per_year.append(
            {
                "fiscal_year": chosen["fiscal_year"],
                "quarter": chosen["quarter"],
                "status": "ok" if rows else "gap",
                "matched_employer_names": meta["employers_matched"],
                **summarise(rows),
            }
        )

    if args.csv_out and all_rows:
        with open(Path(args.csv_out).expanduser(), "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=sorted(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)

    total = len(all_rows)
    payload = {
        "employer_query": args.employer,
        "title_filter": args.title,
        "state_filter": args.state,
        "files_used": used,
        "years": per_year,
        "combined": summarise(all_rows) if all_rows else {},
        "signals": {
            "sponsorship_history_3y": {
                "value": total,
                "confidence": "high" if used else "none",
                "note": (
                    f"{total} LCA filings across FY"
                    + ", FY".join(str(f["fiscal_year"]) for f in used)
                    if used else "no disclosure file could be read"
                ),
            }
        },
        "caveat": (
            "LCA wages are the minimum the employer commits to pay for the role, not total "
            "compensation, and cover only sponsored hires. Read them as a floor."
        ),
        "source_url": PERFORMANCE_PAGE,
        "retrieved_at": common.iso_now(),
    }
    if not all_rows:
        payload["gaps"] = [
            {
                "reason": f"no LCA filings found for employer matching {args.employer!r}",
                "suggested_fallback": (
                    "check the legal name as filed (try --list-files then search the raw file), "
                    "or accept that this employer does not sponsor US visas"
                ),
            }
        ]
    common.emit(payload, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
