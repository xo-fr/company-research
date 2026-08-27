"""Reconstruct a company's hiring history from the Internet Archive.

    python scripts/wayback_jobs.py --domain linear.app
    python scripts/wayback_jobs.py --domain acme.com --path /careers --from 2024-01
    python scripts/wayback_jobs.py --url "boards.greenhouse.io/acme*" --samples 24

Local snapshots only start the day the tool is first run. The Wayback CDX index is the
only free way to answer "were they hiring like this a year ago?" retroactively, which is
the difference between "they have 40 openings" and "they had 120 openings last year and
have been shrinking since".

Archived pages never change, so captures are cached permanently and re-runs are free.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

CDX = "http://web.archive.org/cdx/search/cdx"
REPLAY = "https://web.archive.org/web/{timestamp}id_/{url}"


def cdx_captures(pattern: str, since: str | None, cache_dir, limit: int = 400) -> list[dict]:
    params = {
        "url": pattern,
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",  # one capture per month at most
        "limit": str(limit),
        "fl": "timestamp,original,digest,length",
    }
    if since:
        params["from"] = since.replace("-", "")
    url = CDX + "?" + urllib.parse.urlencode(params)
    try:
        rows = common.http_get(
            url, ttl_seconds=common.cache_ttl("snapshots"), cache_dir=cache_dir, timeout=90
        ).json()
    except (common.SourceError, ValueError) as exc:
        raise common.SourceError(f"CDX query failed: {exc}")
    if not rows or len(rows) < 2:
        return []
    header, *body = rows
    return [dict(zip(header, row)) for row in body]


def _index_rank(capture: dict) -> tuple:
    """Prefer the board index over an individual job page.

    A wildcard CDX query returns mostly job-detail URLs, and counting postings on a
    single posting's page always yields one. Fewer path segments and no numeric id is
    what an index looks like.
    """
    path = urllib.parse.urlsplit(capture.get("original", "")).path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    has_id = any(re.fullmatch(r"\d{4,}", s) for s in segments)
    return (1 if has_id else 0, len(segments), -int(capture.get("length") or 0))


def pick_samples(captures: list[dict], samples: int) -> list[dict]:
    """One capture per month -- the most index-like one -- spread evenly if still too many."""
    by_month: "OrderedDict[str, dict]" = OrderedDict()
    for capture in sorted(captures, key=lambda c: c["timestamp"]):
        month = capture["timestamp"][:6]
        current = by_month.get(month)
        if current is None or _index_rank(capture) < _index_rank(current):
            by_month[month] = capture
    picked = sorted(by_month.values(), key=lambda c: c["timestamp"])
    if len(picked) <= samples:
        return picked
    step = len(picked) / samples
    spread = [picked[int(i * step)] for i in range(samples - 1)]
    spread.append(picked[-1])  # always keep the most recent
    return spread


def count_jobs(html: str, base_url: str) -> tuple[int, str]:
    """Count distinct job postings on an archived page.

    Archived HTML is what the crawler saw, so JavaScript boards come back empty. Two
    complementary methods run, and the one with more evidence wins; the method is
    reported so a reader can discount a weak number.
    """
    cleaned = re.sub(r'href="/web/\d+[a-z_]*/', 'href="', html)
    links = snapshot_mod.roles_from_html(cleaned, base_url)
    link_count = len(links)

    # Boards embedded as JSON (Greenhouse, Lever, Ashby, Workday) leave their payload in
    # the page even when the DOM is built later.
    json_ids = set(re.findall(r'"(?:id|jobId|requisitionId)"\s*:\s*"?(\d{4,})', html))
    json_count = len(json_ids)

    if json_count > link_count:
        return json_count, "embedded-json"
    return link_count, "links"


def series(pattern: str, since: str | None, samples: int, cache_dir, quiet: bool = False) -> dict:
    # Ask for the board index itself first. A wildcard query returns thousands of
    # job-detail URLs and, under the row limit, never reaches the recent months at all.
    index_pattern = pattern.rstrip("*")
    captures = cdx_captures(index_pattern, since, cache_dir) if index_pattern != pattern else []
    used_pattern = index_pattern if captures else pattern
    if not captures:
        captures = cdx_captures(pattern, since, cache_dir)
    if not captures:
        return {
            "status": "gap",
            "pattern": pattern,
            "reason": "no Wayback captures matched that URL pattern",
            "suggested_fallback": "try --path /jobs, or point --url at the ATS board (e.g. boards.greenhouse.io/<slug>*)",
        }
    picked = pick_samples(captures, samples)
    points = []
    for index, capture in enumerate(picked, 1):
        replay = REPLAY.format(timestamp=capture["timestamp"], url=capture["original"])
        if not quiet:
            print(f"\r  fetching capture {index}/{len(picked)} ({capture['timestamp'][:6]})", end="", file=sys.stderr, flush=True)
        try:
            html = common.http_get(
                replay, ttl_seconds=common.cache_ttl("snapshots"), cache_dir=cache_dir, timeout=90, attempts=2
            ).text
        except common.SourceError as exc:
            points.append({"timestamp": capture["timestamp"], "date": _fmt(capture["timestamp"]), "error": str(exc)})
            continue
        count, method = count_jobs(html, capture["original"])
        points.append(
            {
                "timestamp": capture["timestamp"],
                "date": _fmt(capture["timestamp"]),
                "job_count": count,
                "method": method,
                "archived_url": f"https://web.archive.org/web/{capture['timestamp']}/{capture['original']}",
            }
        )
    if not quiet:
        print("", file=sys.stderr)

    usable = [p for p in points if p.get("job_count")]
    result = {
        "status": "ok" if usable else "partial",
        "pattern": used_pattern,
        "wildcard_pattern": pattern,
        "captures_indexed": len(captures),
        "captures_sampled": len(picked),
        "first_capture": _fmt(picked[0]["timestamp"]),
        "latest_capture": _fmt(picked[-1]["timestamp"]),
        "months_covered": len({p["date"][:7] for p in points}),
        "series": points,
        "source_url": f"http://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(pattern)}&output=json",
        "caveat": (
            "counts come from what the crawler captured on the board page, which for a "
            "paginated board is the first page only. Read the series as a consistent "
            "proxy for direction, not as the absolute number of open roles."
        ),
    }
    result.update(trend(usable))
    if not usable:
        census = url_census(pattern, since, cache_dir)
        result["url_census"] = census
        if census.get("series"):
            result["status"] = "partial"
            result.update(trend([{"date": p["date"], "job_count": p["job_count"]} for p in census["series"]]))
            for signal in result.get("signals", {}).values():
                signal["confidence"] = "low"
                signal["note"] = (
                    "counted from distinct archived job URLs per month, not from a board "
                    "page: archive crawl intensity varies, so read the shape, not the level"
                )
        result["gaps"] = [
            {
                "reason": "captures exist but no board page could be counted "
                          "(the board renders in JavaScript, which the crawler did not run)",
                "suggested_fallback": (
                    "run again with --url pointed at the board index, or rely on local "
                    "snapshots from snapshot.py going forward"
                ),
            }
        ]
    return result


def url_census(pattern: str, since: str | None, cache_dir) -> dict:
    """Fallback: how many distinct job URLs the archive saw each month.

    Weaker than counting a board page -- it measures the crawler as much as the company
    -- but it is the only signal left when every capture is a JavaScript shell, and the
    month-to-month *shape* still carries information.
    """
    try:
        rows = cdx_captures_raw(pattern, since, cache_dir)
    except common.SourceError as exc:
        return {"status": "gap", "reason": str(exc)}
    per_month: dict[str, set] = {}
    for row in rows:
        path = urllib.parse.urlsplit(row.get("original", "")).path
        if not re.search(r"/(jobs?|positions?|openings?)/", path):
            continue
        ident = re.sub(r"[?#].*$", "", path)
        per_month.setdefault(row["timestamp"][:6], set()).add(ident)
    series_points = [
        {"date": f"{month[:4]}-{month[4:6]}-15", "job_count": len(ids), "method": "cdx-census"}
        for month, ids in sorted(per_month.items())
    ]
    return {
        "status": "ok" if series_points else "gap",
        "method": "cdx-census",
        "series": series_points,
        "caveat": "distinct archived job URLs per month; a proxy, not a count of open roles",
    }


def cdx_captures_raw(pattern: str, since: str | None, cache_dir, limit: int = 8000) -> list[dict]:
    params = {
        "url": pattern,
        "output": "json",
        "filter": "statuscode:200",
        "limit": str(limit),
        "fl": "timestamp,original",
    }
    if since:
        params["from"] = since.replace("-", "")
    url = CDX + "?" + urllib.parse.urlencode(params)
    try:
        rows = common.http_get(
            url, ttl_seconds=common.cache_ttl("snapshots"), cache_dir=cache_dir, timeout=120
        ).json()
    except (common.SourceError, ValueError) as exc:
        raise common.SourceError(f"CDX census query failed: {exc}")
    if not rows or len(rows) < 2:
        return []
    header, *body = rows
    return [dict(zip(header, row)) for row in body]


def _fmt(timestamp: str) -> str:
    return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def trend(points: list[dict]) -> dict:
    if len(points) < 2:
        return {"signals": {}}
    latest = points[-1]
    latest_date = date.fromisoformat(latest["date"])

    def nearest(days: int) -> dict | None:
        target = latest_date.toordinal() - days
        candidates = [p for p in points[:-1]]
        if not candidates:
            return None
        return min(candidates, key=lambda p: abs(date.fromisoformat(p["date"]).toordinal() - target))

    out: dict = {"signals": {}}
    baseline_90 = nearest(90)
    if baseline_90 and baseline_90["job_count"]:
        velocity = (latest["job_count"] - baseline_90["job_count"]) / baseline_90["job_count"]
        gap_days = (latest_date - date.fromisoformat(baseline_90["date"])).days
        out["signals"]["hiring_velocity_90d"] = {
            "value": round(velocity, 3),
            "confidence": "medium" if 45 <= gap_days <= 200 else "low",
            "note": (
                f"{latest['job_count']} openings on {latest['date']} vs "
                f"{baseline_90['job_count']} on {baseline_90['date']} ({gap_days} days apart), "
                "counted from archived captures"
            ),
        }
    baseline_365 = nearest(365)
    if baseline_365 and baseline_365["job_count"]:
        change = (latest["job_count"] - baseline_365["job_count"]) / baseline_365["job_count"]
        out["twelve_month_change"] = {
            "value": round(change, 3),
            "from": {"date": baseline_365["date"], "job_count": baseline_365["job_count"]},
            "to": {"date": latest["date"], "job_count": latest["job_count"]},
            "note": (
                "openings are a proxy for headcount direction, not headcount itself; "
                "use it in the narrative, and only set headcount_trend_12m from a "
                "reported headcount figure"
            ),
        }
    counts = [p["job_count"] for p in points]
    out["summary"] = {
        "min": min(counts),
        "max": max(counts),
        "latest": counts[-1],
        "median": sorted(counts)[len(counts) // 2],
    }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", help="company domain, e.g. acme.com")
    parser.add_argument("--path", default="/careers", help="careers path to match (default /careers)")
    parser.add_argument("--url", help="explicit CDX url pattern, overrides --domain/--path")
    parser.add_argument("--from", dest="since", help="earliest capture, YYYY-MM (default: 24 months ago)")
    parser.add_argument("--samples", type=int, default=18, help="captures to fetch (default 18)")
    parser.add_argument("--quiet", action="store_true")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    if args.url:
        pattern = args.url
    elif args.domain:
        domain = args.domain.strip().lower().lstrip("www.")
        pattern = f"{domain}{args.path.rstrip('/')}*"
    else:
        common.fail("wayback_jobs.py: pass --domain or --url")

    since = args.since
    if not since:
        today = datetime.utcnow()
        since = f"{today.year - 2}-{today.month:02d}"

    try:
        payload = series(pattern, since, args.samples, args.cache_dir, args.quiet)
    except common.SourceError as exc:
        common.emit(
            {
                "status": "gap",
                "pattern": pattern,
                "reason": str(exc),
                "suggested_fallback": "web.archive.org throttles under load; retry, or rely on local snapshots",
                "retrieved_at": common.iso_now(),
            },
            args.pretty,
        )
        return 0  # a dead source is a gap, not a crash (BUILD-SPEC 11.1)

    payload["retrieved_at"] = common.iso_now()
    common.emit(payload, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
