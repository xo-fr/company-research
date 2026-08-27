"""Capture a company's open roles today, and compare against every earlier capture.

    python scripts/snapshot.py --domain stripe.com
    python scripts/snapshot.py --domain acme.com --careers-url https://acme.com/jobs
    python scripts/snapshot.py --watchlist            # every domain in watchlist.txt
    python scripts/snapshot.py --domain acme.com --report-only

Appends one line per day to ``~/.company-research/snapshots/{domain}/careers.jsonl`` and
is idempotent within a day. Every dossier run calls it, so the data accumulates for free
from ordinary use -- no cron required, though ``--print-cron`` will hand you one.

The reason this exists: a role that is posted, filled, and posted again three times in a
year says something about that team that no review site will tell you. That pattern is
only visible if somebody was writing down the postings all along.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

CAREERS_PATHS = ["/careers", "/jobs", "/careers/jobs", "/company/careers", "/about/careers", "/join-us"]

# Public, keyless job-board APIs. Where a company uses one of these, the role list is
# exact -- titles, locations and stable ids -- instead of scraped from rendered HTML.
ATS_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-zA-Z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)"),
    ("recruitee", r"([a-zA-Z0-9_-]+)\.recruitee\.com"),
    ("workable", r"apply\.workable\.com/([a-zA-Z0-9_-]+)"),
]

JOB_LINK_RE = re.compile(
    r"""href=["']([^"']*(?:/jobs?/[^"']*\d[^"']*|/careers?/[^"']*\d[^"']*|/job-detail[^"']*|"""
    r"""/positions?/[^"']*))["']""",
    re.I,
)


# --------------------------------------------------------------------- discovery


def careers_url(domain: str, cache_dir, given: str | None = None) -> tuple[str | None, list[str]]:
    """Find the careers page. Returns (url, notes)."""
    notes: list[str] = []
    if given:
        return given, notes
    root = f"https://{domain}"
    try:
        home = common.http_get(root, ttl_seconds=common.cache_ttl("default"), cache_dir=cache_dir).text
        for href in re.findall(r'href=["\']([^"\']+)["\']', home):
            if re.search(r"(?i)(career|jobs|join.?us|we.?re.?hiring)", href):
                return urllib.parse.urljoin(root, href), notes
    except common.SourceError as exc:
        notes.append(f"homepage unreachable: {exc}")
    for path in CAREERS_PATHS:
        candidate = root + path
        try:
            resp = common.http_get(candidate, ttl_seconds=common.cache_ttl("default"), cache_dir=cache_dir)
            if resp.ok:
                return candidate, notes
        except common.SourceError:
            continue
    notes.append("no careers page found at the usual paths")
    return None, notes


def detect_ats(html: str) -> tuple[str | None, str | None]:
    for name, pattern in ATS_PATTERNS:
        match = re.search(pattern, html)
        if match:
            return name, match.group(1)
    return None, None


# ------------------------------------------------------------------ role fetching


def roles_from_ats(kind: str, slug: str, cache_dir) -> list[dict]:
    ttl = common.cache_ttl("news")  # postings move daily; do not serve a stale board
    try:
        if kind == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("title", ""),
                    "location": (job.get("location") or {}).get("name", ""),
                    "posted_id": str(job.get("id")),
                    "url": job.get("absolute_url"),
                    "updated_at": job.get("updated_at"),
                }
                for job in data.get("jobs", [])
            ]
        if kind == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("text", ""),
                    "location": (job.get("categories") or {}).get("location", ""),
                    "posted_id": str(job.get("id")),
                    "url": job.get("hostedUrl"),
                    "updated_at": job.get("createdAt"),
                }
                for job in data
            ]
        if kind == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("title", ""),
                    "location": job.get("location", ""),
                    "posted_id": str(job.get("id")),
                    "url": job.get("jobUrl"),
                    "updated_at": job.get("publishedAt"),
                }
                for job in data.get("jobs", [])
            ]
        if kind == "smartrecruiters":
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("name", ""),
                    "location": ", ".join(
                        filter(None, [(job.get("location") or {}).get("city"), (job.get("location") or {}).get("country")])
                    ),
                    "posted_id": str(job.get("id")),
                    "url": (job.get("ref") or {}).get("jobAd"),
                    "updated_at": job.get("releasedDate"),
                }
                for job in data.get("content", [])
            ]
        if kind == "recruitee":
            url = f"https://{slug}.recruitee.com/api/offers/"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("title", ""),
                    "location": job.get("location", ""),
                    "posted_id": str(job.get("id")),
                    "url": job.get("careers_url"),
                    "updated_at": job.get("published_at"),
                }
                for job in data.get("offers", [])
            ]
        if kind == "workable":
            url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
            data = common.http_get(url, ttl_seconds=ttl, cache_dir=cache_dir).json()
            return [
                {
                    "title": job.get("title", ""),
                    "location": job.get("location", {}).get("location_str", "")
                    if isinstance(job.get("location"), dict) else str(job.get("location") or ""),
                    "posted_id": str(job.get("shortcode") or job.get("id")),
                    "url": job.get("url"),
                    "updated_at": job.get("published_on"),
                }
                for job in data.get("jobs", [])
            ]
    except (common.SourceError, ValueError, AttributeError, TypeError):
        return []
    return []


def roles_from_html(html: str, base_url: str) -> list[dict]:
    """Last resort: count distinct job-detail links on the rendered careers page.

    Careers pages that render entirely in JavaScript will yield nothing here. That is a
    real limit and is reported as such rather than papered over with a zero.
    """
    seen: dict[str, dict] = {}
    for href in JOB_LINK_RE.findall(html):
        url = urllib.parse.urljoin(base_url, href)
        if url in seen or "#" == href.strip():
            continue
        slug = re.sub(r"[-_/]+", " ", urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
        slug = re.sub(r"\b\d{4,}\b", "", slug).strip()
        seen[url] = {"title": slug.title() or url, "location": "", "posted_id": url, "url": url}
    return list(seen.values())


def capture(domain: str, cache_dir, given_url: str | None = None) -> dict:
    url, notes = careers_url(domain, cache_dir, given_url)
    record: dict = {
        "ts": common.iso_now(),
        "date": common.today(),
        "domain": domain,
        "careers_url": url,
        "method": None,
        "count": 0,
        "roles": [],
        "notes": notes,
    }
    if not url:
        record["method"] = "none"
        return record
    try:
        html = common.http_get(url, ttl_seconds=common.cache_ttl("news"), cache_dir=cache_dir).text
    except common.SourceError as exc:
        record["method"] = "none"
        record["notes"].append(f"careers page unreachable: {exc}")
        return record

    kind, slug = detect_ats(html)
    if not kind:
        # Most careers pages render their board in JavaScript, so the ATS link is absent
        # from the raw HTML. The reader service returns the rendered text, links included.
        try:
            kind, slug = detect_ats(common.extract_text(url, cache_dir=cache_dir, ttl_seconds=common.cache_ttl("news")))
        except common.SourceError:
            pass
    roles: list[dict] = []
    if kind:
        roles = roles_from_ats(kind, slug, cache_dir)
        record["method"] = f"ats:{kind}"
        record["ats"] = {"kind": kind, "slug": slug}
    if not roles:
        # Last resort before giving up: try the brand token as an ATS slug. A wrong guess
        # returns nothing, so this can add data but never invent it.
        guess = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
        for candidate in ("greenhouse", "lever", "ashby"):
            found = roles_from_ats(candidate, guess, cache_dir)
            if found:
                kind, slug, roles = candidate, guess, found
                record["method"] = f"ats:{candidate}"
                record["ats"] = {"kind": candidate, "slug": guess, "slug_guessed": True}
                break
    if not roles:
        roles = roles_from_html(html, url)
        record["method"] = (record["method"] + "+html") if kind else "html"
    if not roles:
        record["notes"].append(
            "no roles parsed: the page probably renders its board in JavaScript. "
            "Pass --careers-url pointing at the board itself (e.g. the Greenhouse URL)."
        )
    record["roles"] = roles
    record["count"] = len(roles)
    return record


# ------------------------------------------------------------------- persistence


def snapshot_path(domain: str) -> Path:
    return common.SNAPSHOT_DIR / domain / "careers.jsonl"


def read_snapshots(domain: str) -> list[dict]:
    path = snapshot_path(domain)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_snapshot(domain: str, record: dict, force: bool = False) -> tuple[bool, str]:
    path = snapshot_path(domain)
    existing = read_snapshots(domain)
    if not force and any(s.get("date") == record["date"] for s in existing):
        return False, "already captured today"
    # first_seen carries forward, so a role's true age survives across snapshots.
    previous_first_seen = {}
    for snap in existing:
        for role in snap.get("roles", []):
            key = role_key(role)
            previous_first_seen.setdefault(key, role.get("first_seen") or snap.get("date"))
    for role in record["roles"]:
        key = role_key(role)
        role["first_seen"] = previous_first_seen.get(key, record["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True, str(path)


def role_key(role: dict) -> str:
    return common.normalize_title(role.get("title", "")) + "|" + _norm_location(role.get("location", ""))


_COUNTRY_WORDS = {
    "india", "usa", "us", "united", "states", "america", "uk", "kingdom", "canada",
    "germany", "ireland", "singapore", "japan", "australia", "france", "netherlands",
    "poland", "brazil", "mexico", "israel", "china", "emea", "apac", "amer",
}
_CITY_ALIASES = {
    "bengaluru": "bangalore", "gurugram": "gurgaon", "bombay": "mumbai",
    "calcutta": "kolkata", "madras": "chennai", "sf": "san francisco", "nyc": "new york",
}


def _norm_location(location: str) -> str:
    """Fold a posting's location to a comparable city.

    Boards spell the same office five ways -- "Bengaluru, India", "Bangalore",
    "Bengaluru, KA, India", "Remote - India". Comparing them raw makes one role look
    like four, which silently destroys the repost count this file exists to produce.
    """
    raw = (location or "").lower()
    segment = re.split(r"[,;/|]|\s+-\s+", raw)[0]
    words = [w for w in re.sub(r"[^a-z ]+", " ", segment).split() if w not in _COUNTRY_WORDS]
    words = [_CITY_ALIASES.get(w, w) for w in words]
    if not words:
        return "remote" if "remote" in raw else ""
    if words[0] in ("remote", "hybrid", "onsite"):
        rest = [w for w in words[1:] if w not in ("remote", "hybrid", "onsite")]
        return " ".join(rest[:2]) if rest else "remote"
    return " ".join(words[:2])


# ---------------------------------------------------------------------- analysis


def _days_between(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days)
    except ValueError:
        return 0


def analyse(domain: str, role_filter: str | None = None, window_days: int = 365) -> dict:
    """Repost cycles and hiring velocity from the local snapshot history."""
    snaps = sorted(read_snapshots(domain), key=lambda s: s.get("date", ""))
    if not snaps:
        return {"status": "gap", "reason": f"no snapshots recorded for {domain} yet"}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    windowed = [s for s in snaps if s.get("date", "") >= cutoff] or snaps

    # A repost = the role disappears from the board and comes back later.
    presence: dict[str, list[bool]] = {}
    titles: dict[str, str] = {}
    for snap in windowed:
        keys = {role_key(r) for r in snap.get("roles", [])}
        for key in keys | set(presence):
            presence.setdefault(key, [False] * (windowed.index(snap)))
            presence[key].append(key in keys)
        for role in snap.get("roles", []):
            titles.setdefault(role_key(role), role.get("title", ""))

    reposts: list[dict] = []
    for key, series in presence.items():
        cycles, previous = 0, False
        for present in series:
            if present and not previous:
                cycles += 1
            previous = present
        if cycles >= 2:
            reposts.append({"role": titles.get(key, key), "key": key, "postings": cycles})
    reposts.sort(key=lambda r: r["postings"], reverse=True)

    counts = [(s["date"], s.get("count", 0)) for s in windowed]
    velocity = None
    if len(counts) >= 2:
        latest_date, latest = counts[-1]
        baseline_date, baseline = counts[0]
        for when, value in counts:
            if _days_between(when, latest_date) <= 90:
                baseline_date, baseline = when, value
                break
        if baseline:
            velocity = round((latest - baseline) / baseline, 3)

    focus = None
    if role_filter:
        wanted = common.normalize_title(role_filter)
        matches = [r for r in reposts if wanted and wanted in r["key"]]
        focus = matches[0] if matches else {"role": role_filter, "postings": 1, "note": "seen once or never"}

    return {
        "status": "ok",
        "domain": domain,
        "snapshots": len(snaps),
        "window_days": window_days,
        "first_snapshot": snaps[0].get("date"),
        "latest_snapshot": snaps[-1].get("date"),
        "openings_series": counts,
        "reposted_roles": reposts[:20],
        "signals": {
            "role_repost_count_12m": {
                "value": (focus or {}).get("postings") if focus else (reposts[0]["postings"] if reposts else None),
                "confidence": "high" if len(windowed) >= 4 else "low" if len(windowed) >= 2 else "none",
                "note": (
                    f"across {len(windowed)} snapshots between {windowed[0].get('date')} and "
                    f"{windowed[-1].get('date')}"
                ),
            },
            "hiring_velocity_90d": {
                "value": velocity,
                "confidence": "medium" if velocity is not None and len(counts) >= 3 else "none",
                "note": "net change in open roles over the last ~90 days, relative to the baseline count",
            },
        },
    }


CRON_COMMENT = "# company-research watchlist snapshot"


def cron_line() -> str:
    script = Path(__file__).resolve()
    return f"0 7 * * 1 {sys.executable} {script} --watchlist --quiet  {CRON_COMMENT}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain")
    parser.add_argument("--careers-url")
    parser.add_argument("--watchlist", action="store_true", help=f"snapshot every domain in {common.WATCHLIST_PATH}")
    parser.add_argument("--role", help="focus the repost count on one role title")
    parser.add_argument("--report-only", action="store_true", help="analyse history without capturing")
    parser.add_argument("--force", action="store_true", help="capture again even if today is already recorded")
    parser.add_argument("--window-days", type=int, default=365)
    parser.add_argument("--print-cron", action="store_true", help="print a weekly crontab line and exit")
    parser.add_argument("--quiet", action="store_true")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    if args.print_cron:
        common.emit(
            {
                "crontab_line": cron_line(),
                "install_hint": "crontab -l | { cat; echo '<line>'; } | crontab -   (macOS/Linux)",
                "windows_hint": (
                    'schtasks /create /tn "company-research snapshot" /sc weekly /d MON /st 07:00 '
                    f'/tr "{sys.executable} {Path(__file__).resolve()} --watchlist --quiet"'
                ),
                "watchlist_path": str(common.WATCHLIST_PATH),
                "note": "opportunistic snapshots happen on every dossier run; a cron is optional.",
            },
            args.pretty,
        )
        return 0

    domains: list[str] = []
    if args.watchlist:
        if not common.WATCHLIST_PATH.exists():
            common.fail(f"snapshot.py: no watchlist at {common.WATCHLIST_PATH}")
        domains = [
            line.strip() for line in common.WATCHLIST_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    elif args.domain:
        domains = [args.domain.strip().lower().lstrip("www.")]
    else:
        common.fail("snapshot.py: pass --domain, --watchlist or --print-cron")

    results = []
    for domain in domains:
        entry: dict = {"domain": domain}
        if not args.report_only:
            record = capture(domain, args.cache_dir, args.careers_url if len(domains) == 1 else None)
            written, detail = append_snapshot(domain, record, args.force)
            entry["captured"] = written
            entry["detail"] = detail
            entry["count"] = record["count"]
            entry["method"] = record["method"]
            entry["careers_url"] = record["careers_url"]
            entry["notes"] = record["notes"]
        entry["analysis"] = analyse(domain, args.role, args.window_days)
        results.append(entry)
        if not args.quiet:
            print(
                f"{domain}: {entry.get('count', '-')} open roles"
                f" via {entry.get('method', '-')}"
                f" ({entry.get('detail', 'report only')})",
                file=sys.stderr,
            )

    common.emit(results[0] if len(results) == 1 else {"results": results}, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
