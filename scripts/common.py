"""Shared plumbing for every company-research script.

Deterministic only: on-disk cache, per-domain rate limiting, polite identification,
retries, text extraction, and a tiny YAML reader for ``profile.yaml``.

No LLM calls live here, or anywhere else in ``scripts/``. See docs/BUILD-SPEC.md section 2.
"""

from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VERSION = "0.1.0"
REPO_URL = "https://github.com/xo-fr/company-research"
SPEC_VERSION = "1.0.0"

HOME = Path(os.environ.get("CR_HOME", Path.home() / ".company-research")).expanduser()
DEFAULT_CACHE_DIR = HOME / "cache"
PROFILE_PATH = HOME / "profile.yaml"
SNAPSHOT_DIR = HOME / "snapshots"
DOSSIER_DIR = HOME / "dossiers"
WATCHLIST_PATH = HOME / "watchlist.txt"

NEVER_EXPIRES = -1

# BUILD-SPEC 11.3. Keys are source classes, values seconds.
_TTL = {
    "entity": 90 * 86400,
    "filings": 30 * 86400,
    "compensation": 30 * 86400,
    "reviews": 7 * 86400,
    "github": 7 * 86400,
    "interview": 14 * 86400,
    "news": 6 * 3600,
    "snapshots": NEVER_EXPIRES,
    "default": 86400,
}

# BUILD-SPEC 11.2: SEC allows 10 req/sec, everything else gets 2.
_RATE_LIMITS = {
    "www.sec.gov": 10.0,
    "data.sec.gov": 10.0,
    "efts.sec.gov": 10.0,
}
_DEFAULT_RATE = 2.0

_SEC_DOMAINS = {"www.sec.gov", "data.sec.gov", "efts.sec.gov"}

# Documented API surfaces. Wikimedia, SEC and others disallow their API paths in
# robots.txt because those files are written for crawlers and mirrors; the endpoints
# below are published for programmatic clients and carry their own etiquette rules
# (identify yourself, stay under the rate limit), which this client follows instead.
# Everything else -- company sites, careers pages, news, review sites -- is fetched
# only when robots.txt allows it.
_API_ENDPOINTS = {
    "www.wikidata.org": ("/w/api.php",),
    "query.wikidata.org": ("/sparql",),
    "www.sec.gov": ("/files/", "/cgi-bin/browse-edgar", "/Archives/"),
    "data.sec.gov": ("/",),
    "efts.sec.gov": ("/",),
    "api.gdeltproject.org": ("/",),
    "hn.algolia.com": ("/api/",),
    "web.archive.org": ("/cdx/",),
    "api.github.com": ("/",),
    "r.jina.ai": ("/",),
    "api.bseindia.com": ("/",),
    "www.nseindia.com": ("/api/",),
}


def is_api_endpoint(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    prefixes = _API_ENDPOINTS.get(parts.netloc)
    return bool(prefixes) and parts.path.startswith(tuple(prefixes))


_last_request: dict[str, float] = {}
_robots_cache: dict[str, Any] = {}


class SourceError(RuntimeError):
    """A source failed in a way the caller should report, not crash on."""


class MissingContactEmail(RuntimeError):
    """SEC fair-access policy requires a contact address in the User-Agent."""


# --------------------------------------------------------------------------- time


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cache_ttl(source_class: str) -> int:
    """Seconds a cached body for this class of source stays fresh (11.3)."""
    return _TTL.get(source_class, _TTL["default"])


# ------------------------------------------------------------------------ signals

# BUILD-SPEC section 6. One registry, three consumers: merge.py validates against it,
# render.py embeds it in the dossier, and the dashboard's JavaScript normalises with it.
# Normalisation is data, not code, so the browser and the tests cannot drift apart.
#
#   affine : clamp(v * scale + offset, lo, hi)
#   table  : first row whose threshold matches wins ("le" = v <= t, "lt" = v < t)
#   enum   : exact lookup, with false/true spelled out for booleans
SIGNALS: dict[str, dict] = {
    "layoff_events_24m": {
        "dimension": "stability", "direction": "lower_better", "raw": "int",
        "label": "Layoff events (24m)",
        "normalize": {"kind": "table", "cmp": "le", "rows": [[0, 1.0], [1, 0.6], [2, 0.3]], "else": 0.1},
    },
    "funding_months_ago": {
        "dimension": "stability", "direction": "lower_better", "raw": "int",
        "label": "Months since last raise", "applies_to": "private companies",
        "normalize": {"kind": "table", "cmp": "lt", "rows": [[12, 1.0], [24, 0.7], [36, 0.4]], "else": 0.15},
    },
    "revenue_trend": {
        "dimension": "stability", "direction": "higher_better", "raw": "enum",
        "label": "Revenue trend",
        "normalize": {"kind": "enum", "map": {"growing": 1.0, "flat": 0.5, "declining": 0.1}},
    },
    "role_repost_count_12m": {
        "dimension": "stability", "direction": "lower_better", "raw": "int",
        "label": "Times this role was reposted (12m)",
        "normalize": {"kind": "table", "cmp": "le", "rows": [[1, 1.0], [2, 0.7], [3, 0.4]], "else": 0.15},
    },
    "hiring_velocity_90d": {
        "dimension": "growth", "direction": "higher_better", "raw": "float",
        "label": "Hiring velocity (90d)",
        "normalize": {"kind": "affine", "scale": 1.0, "offset": 0.5, "clamp": [0, 1]},
    },
    "headcount_trend_12m": {
        "dimension": "growth", "direction": "higher_better", "raw": "float",
        "label": "Headcount trend (12m)",
        "normalize": {"kind": "affine", "scale": 2.0, "offset": 0.5, "clamp": [0, 1]},
    },
    "comp_percentile_vs_market": {
        "dimension": "comp", "direction": "higher_better", "raw": "int_0_100",
        "label": "Pay percentile vs market",
        "normalize": {"kind": "affine", "scale": 0.01, "offset": 0.0, "clamp": [0, 1]},
    },
    "comp_transparency": {
        "dimension": "comp", "direction": "higher_better", "raw": "bool",
        "label": "Publishes pay ranges",
        "normalize": {"kind": "enum", "map": {"true": 1.0, "false": 0.0}},
    },
    "rating_current": {
        "dimension": "wlb", "direction": "higher_better", "raw": "float_1_5",
        "label": "Employee rating (current)",
        "normalize": {"kind": "affine", "scale": 0.5, "offset": -1.25, "clamp": [0, 1]},
    },
    "rating_trend_24m": {
        "dimension": "wlb", "direction": "higher_better", "raw": "float_delta",
        "label": "Rating trend (24m)",
        "normalize": {"kind": "affine", "scale": 1.0, "offset": 0.5, "clamp": [0, 1]},
    },
    "wlb_sentiment": {
        "dimension": "wlb", "direction": "higher_better", "raw": "float_-1_1",
        "label": "Work-life sentiment",
        "normalize": {"kind": "affine", "scale": 0.5, "offset": 0.5, "clamp": [0, 1]},
    },
    "eng_output_signal": {
        "dimension": "learning", "direction": "higher_better", "raw": "float_0_1",
        "label": "Engineering output in the open",
        "normalize": {"kind": "affine", "scale": 1.0, "offset": 0.0, "clamp": [0, 1]},
    },
    "stack_currency": {
        "dimension": "learning", "direction": "higher_better", "raw": "float_0_1",
        "label": "How current the stack is",
        "normalize": {"kind": "affine", "scale": 1.0, "offset": 0.0, "clamp": [0, 1]},
    },
    "sponsorship_history_3y": {
        "dimension": "logistics", "direction": "higher_better", "raw": "int",
        "label": "Visa sponsorships filed (3y)",
        "conditional_on": "profile.work_authorization requires sponsorship",
        "normalize": {"kind": "table", "cmp": "le", "rows": [[0, 0.0], [9, 0.5], [49, 0.8]], "else": 1.0},
    },
}

DIMENSIONS = ["stability", "comp", "wlb", "learning", "growth", "logistics"]

DIMENSION_LABELS = {
    "stability": "Stability",
    "comp": "Compensation",
    "wlb": "Work-life balance",
    "learning": "Learning",
    "growth": "Growth",
    "logistics": "Logistics",
}

PILLARS = [
    "overview", "news", "hiring_trend", "culture", "compensation", "reviews",
    "interview_prep", "financial_health", "interviewers", "jd_gap",
]

STAGE_PILLARS = {
    "applying": ["overview", "news", "financial_health", "culture", "hiring_trend"],
    "interviewing": ["overview", "interview_prep", "jd_gap", "interviewers", "culture", "news"],
    "offer": ["compensation", "financial_health", "reviews", "hiring_trend", "culture"],
}


def normalize_signal(signal_id: str, value) -> float | None:
    """Python twin of the dashboard's normaliser, used by tests and by merge.py."""
    spec = SIGNALS.get(signal_id)
    if spec is None or value is None:
        return None
    rule = spec["normalize"]
    kind = rule["kind"]
    if kind == "enum":
        key = str(value).lower() if not isinstance(value, bool) else ("true" if value else "false")
        return rule["map"].get(key)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if kind == "affine":
        lo, hi = rule.get("clamp", [0, 1])
        return max(lo, min(hi, numeric * rule["scale"] + rule["offset"]))
    if kind == "table":
        for threshold, out in rule["rows"]:
            if (numeric <= threshold) if rule["cmp"] == "le" else (numeric < threshold):
                return out
        return rule["else"]
    return None


# ------------------------------------------------------------------------ profile


def _yaml_scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d*\.\d+", raw):
        return float(raw)
    return raw


def parse_simple_yaml(text: str) -> dict:
    """Parse the subset of YAML that ``profile.yaml`` uses.

    Nested maps, lists of scalars, ``#`` comments, quoted or bare scalars. Enough for
    BUILD-SPEC section 9 and nothing more; it exists so the tool needs no runtime
    YAML dependency.
    """
    root: dict = {}
    # (indent, parent_dict, key_of_pending_child) for open blocks
    stack: list[tuple[int, dict, str | None]] = [(-1, root, None)]

    def container_for(indent: int) -> tuple[dict, str | None]:
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        return stack[-1][1], stack[-1][2]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", raw_line.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        parent, pending_key = container_for(indent)

        if body.startswith("- "):
            item = _yaml_scalar(body[2:])
            if pending_key is not None:
                bucket = parent.get(pending_key)
                if not isinstance(bucket, list):
                    bucket = []
                    parent[pending_key] = bucket
                bucket.append(item)
            continue

        if ":" not in body:
            continue
        key, _, rest = body.partition(":")
        key = key.strip()
        rest = rest.strip()
        target = parent
        if pending_key is not None:
            child = parent.get(pending_key)
            if not isinstance(child, dict):
                child = {}
                parent[pending_key] = child
            target = child
        if rest == "":
            stack.append((indent, target, key))
        else:
            target[key] = _yaml_scalar(rest)
    return root


def load_profile(path: Path | str | None = None) -> dict:
    p = Path(path).expanduser() if path else PROFILE_PATH
    if not p.exists():
        return {}
    try:
        return parse_simple_yaml(p.read_text(encoding="utf-8")) or {}
    except Exception:  # a malformed profile must never take down a research run
        return {}


def contact_email(profile: dict | None = None) -> str | None:
    env = os.environ.get("CR_CONTACT_EMAIL")
    if env:
        return env.strip()
    prof = profile if profile is not None else load_profile()
    value = prof.get("contact_email")
    return str(value).strip() if value else None


def user_agent(contact: str | None = None) -> str:
    base = f"company-research/{VERSION} (+{REPO_URL})"
    return f"{base} {contact}" if contact else base


# Some WAFs (SEC, BSE) reject any User-Agent containing a URL. Those hosts get the
# plain ``product/version [contact]`` form instead -- still honest identification,
# just without the repo link they refuse.
_PLAIN_UA_HOSTS = {
    "www.sec.gov", "data.sec.gov", "efts.sec.gov",
    "api.bseindia.com", "www.bseindia.com", "www.nseindia.com",
}


def plain_user_agent(contact: str | None = None) -> str:
    base = f"company-research/{VERSION}"
    return f"{base} {contact}" if contact else base


def sec_user_agent(contact: str) -> str:
    """SEC's fair-access notice asks for ``product/version contact``."""
    return plain_user_agent(contact)


def sec_headers(profile: dict | None = None) -> dict[str, str]:
    """Headers for SEC endpoints. Fails closed when no contact address is known."""
    contact = contact_email(profile)
    if not contact:
        raise MissingContactEmail(
            "SEC fair-access policy requires a contact email in the User-Agent.\n"
            "Set it once with either:\n"
            "  export CR_CONTACT_EMAIL='you@example.com'\n"
            f"  or add 'contact_email: you@example.com' to {PROFILE_PATH}\n"
            "It is sent only to sec.gov, in the User-Agent header, and nowhere else."
        )
    return {"User-Agent": sec_user_agent(contact), "Accept-Encoding": "gzip, deflate"}


# -------------------------------------------------------------------------- cache


def cache_path(url: str, cache_dir: Path | str) -> Path:
    domain = urllib.parse.urlsplit(url).netloc or "unknown"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / domain / f"{digest}.json"


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    retrieved_at: str = ""
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.text)


def _read_cache(path: Path, ttl_seconds: int) -> Response | None:
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if ttl_seconds != NEVER_EXPIRES:
        age = time.time() - path.stat().st_mtime
        if age > ttl_seconds:
            # Raw bodies are kept indefinitely; staleness only means "refetch".
            return None
    return Response(
        url=blob.get("url", ""),
        status=int(blob.get("status", 0)),
        headers=blob.get("headers", {}),
        text=blob.get("text", ""),
        retrieved_at=blob.get("retrieved_at", ""),
        from_cache=True,
    )


def _write_cache(path: Path, resp: Response) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": resp.url,
        "status": resp.status,
        "headers": resp.headers,
        "text": resp.text,
        "retrieved_at": resp.retrieved_at,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------- rate limiting


def _throttle(domain: str) -> None:
    rate = _RATE_LIMITS.get(domain, _DEFAULT_RATE)
    min_interval = 1.0 / rate
    last = _last_request.get(domain)
    now = time.monotonic()
    if last is not None:
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request[domain] = time.monotonic()


def _robots_allows(url: str, headers: dict[str, str]) -> bool:
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        parser: Any = urllib.robotparser.RobotFileParser()
        try:
            status, _, body = _raw_fetch(f"{origin}/robots.txt", headers, timeout=10)
            if status == 200:
                parser.parse(body.splitlines())
            else:
                parser = None
        except Exception:
            parser = None
        _robots_cache[origin] = parser
    parser = _robots_cache[origin]
    if parser is None:  # no robots.txt, or unreachable -> allowed
        return True
    try:
        return bool(parser.can_fetch(headers.get("User-Agent", user_agent()), url))
    except Exception:
        return True


# --------------------------------------------------------------------------- http


def _raw_fetch(url: str, headers: dict[str, str], timeout: float) -> tuple[int, dict, str]:
    """One HTTP GET. httpx when installed, urllib otherwise."""
    try:
        import httpx  # type: ignore

        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            r = client.get(url, headers=headers)
            return r.status_code, dict(r.headers), r.text
    except ImportError:
        pass
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return fh.status, dict(fh.headers), _decode_body(fh.read(), fh.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return exc.code, dict(exc.headers or {}), _decode_body(raw, exc.headers)


def _decode_body(raw: bytes, headers) -> str:
    """urllib does not undo content encodings; httpx does. Match httpx here."""
    encoding = (headers.get("Content-Encoding") or "").lower() if headers else ""
    try:
        if "gzip" in encoding:
            import gzip

            raw = gzip.decompress(raw)
        elif "deflate" in encoding:
            import zlib

            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        elif "br" in encoding:
            try:
                import brotli  # type: ignore

                raw = brotli.decompress(raw)
            except ImportError:
                pass
    except Exception:
        pass
    charset = None
    if headers is not None and hasattr(headers, "get_content_charset"):
        charset = headers.get_content_charset()
    if not charset:
        # SEC serves cp1252 filings with no charset. Guessing utf-8 turns every curly
        # quote in a risk factor into a replacement character, which then shows up inside
        # quoted claims in the dossier.
        meta = re.search(rb'charset=["\']?([\w-]+)', raw[:4096], re.I)
        charset = meta.group(1).decode("ascii", "ignore") if meta else None
    for candidate in [charset, "utf-8", "cp1252", "latin-1"]:
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def http_get(
    url: str,
    *,
    ttl_seconds: int,
    headers: dict[str, str] | None = None,
    cache_dir: Path | str | None = None,
    timeout: float = 30.0,
    attempts: int = 3,
    respect_robots: bool = True,
    allow_cached_errors: bool = False,
) -> Response:
    """Cached, rate-limited, retrying GET.

    Raises :class:`SourceError` when every attempt fails. Callers turn that into a
    ``gap`` in the evidence rather than a crash (11.1).
    """
    cache_dir = Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_DIR
    path = cache_path(url, cache_dir)
    offline = os.environ.get("CR_OFFLINE") == "1"
    cached = _read_cache(path, NEVER_EXPIRES if offline else ttl_seconds)
    if cached is not None and (cached.ok or allow_cached_errors):
        return cached
    if offline:
        # CR_OFFLINE serves whatever is cached, however old, and never opens a socket.
        # The test suite runs this way; so can a user on a plane.
        raise SourceError(f"offline mode: {url} is not in the cache at {cache_dir}")

    domain = urllib.parse.urlsplit(url).netloc
    hdrs = dict(headers or {})
    if domain in _PLAIN_UA_HOSTS:
        hdrs.setdefault("User-Agent", plain_user_agent(contact_email() if domain in _SEC_DOMAINS else None))
    else:
        hdrs.setdefault("User-Agent", user_agent())
    hdrs.setdefault("Accept-Encoding", "gzip, deflate")

    if respect_robots and not is_api_endpoint(url) and not _robots_allows(url, hdrs):
        raise SourceError(f"robots.txt disallows {url}")

    last_error = ""
    for attempt in range(attempts):
        _throttle(domain)
        try:
            status, resp_headers, text = _raw_fetch(url, hdrs, timeout)
        except Exception as exc:  # network-level failure
            last_error = f"{type(exc).__name__}: {exc}"
            status, resp_headers, text = 0, {}, ""
        if status and 200 <= status < 400:
            resp = Response(url, status, _clean_headers(resp_headers), text, iso_now(), False)
            _write_cache(path, resp)
            return resp
        if status == 429 or status >= 500 or status == 0:
            last_error = last_error or f"HTTP {status}"
            if attempt < attempts - 1:
                time.sleep((2**attempt) + random.random())
                continue
            break
        # 4xx other than 429: retrying will not help
        resp = Response(url, status, _clean_headers(resp_headers), text, iso_now(), False)
        _write_cache(path, resp)
        raise SourceError(f"HTTP {status} for {url}")
    raise SourceError(f"failed after {attempts} attempts: {last_error} ({url})")


def _clean_headers(headers: dict) -> dict[str, str]:
    keep = ("content-type", "last-modified", "date", "etag")
    return {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() in keep}


# ---------------------------------------------------------------- text extraction

_BLOCK_TAGS = r"p|div|br|li|tr|h[1-6]|section|article|header|footer|table|ul|ol|blockquote"


def strip_html(html: str) -> str:
    """Local readability-ish extractor. No dependencies, no network."""
    text = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<\s*/?\s*(" + _BLOCK_TAGS + r")\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def extract_text(
    html_or_url: str,
    *,
    url: str | None = None,
    ttl_seconds: int | None = None,
    cache_dir: Path | str | None = None,
    prefer_reader: bool = True,
) -> str:
    """Readable text for a document.

    Given a URL, tries the keyless Jina reader first and falls back to fetching the page
    and extracting locally. Given HTML, extracts locally. The remote path is never
    required.
    """
    looks_like_url = html_or_url.startswith(("http://", "https://")) and "<" not in html_or_url[:200]
    target = url or (html_or_url if looks_like_url else None)
    if target is None:
        return strip_html(html_or_url)

    ttl = ttl_seconds if ttl_seconds is not None else cache_ttl("default")
    if prefer_reader:
        try:
            resp = http_get(
                "https://r.jina.ai/" + target,
                ttl_seconds=ttl,
                cache_dir=cache_dir,
                headers={"Accept": "text/plain"},
                attempts=1,
                respect_robots=False,
            )
            if resp.ok and len(resp.text.strip()) > 200:
                return resp.text.strip()
        except SourceError:
            pass
    resp = http_get(target, ttl_seconds=ttl, cache_dir=cache_dir)
    return strip_html(resp.text)


# ----------------------------------------------------------------------- cli utils


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help=f"on-disk HTTP cache (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="bypass robots.txt (never used by the skill; manual escape hatch only)",
    )
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


def _use_utf8_streams() -> None:
    """Windows consoles default to cp1252 and blow up on a filing's en dashes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_use_utf8_streams()


def emit(payload: Any, pretty: bool = False) -> None:
    json.dump(payload, sys.stdout, indent=2 if pretty else None, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def gap(reason: str, suggested_fallback: str = "") -> dict:
    """The standard shape for 'this source cannot answer here' (11.1)."""
    out: dict[str, Any] = {"status": "gap", "reason": reason}
    if suggested_fallback:
        out["suggested_fallback"] = suggested_fallback
    return out


_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "llp",
    "ltd", "limited", "plc", "pvt", "private", "gmbh", "sa", "nv", "ag", "holdings",
    "group", "the",
}


def normalize_company_name(name: str) -> str:
    """Fold legal suffixes and punctuation so 'Acme Inc.' matches 'ACME INC'."""
    text = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    words = [w for w in text.split() if w not in _LEGAL_SUFFIXES]
    return " ".join(words) or " ".join(text.split())


def normalize_title(title: str) -> str:
    """Fold a job title for cross-snapshot matching (roles get renamed cosmetically)."""
    text = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    text = re.sub(r"\b(sr|snr)\b", "senior", text)
    text = re.sub(r"\b(jr)\b", "junior", text)
    text = re.sub(r"\beng\b", "engineer", text)
    text = re.sub(r"\bengineering\b", "engineer", text)
    text = re.sub(r"\bsde\b", "software engineer", text)
    text = re.sub(r"\b(remote|hybrid|onsite|full time|fulltime|contract)\b", " ", text)
    text = re.sub(r"\b[ivx]+\b", " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    return " ".join(text.split())


def chunks(items: Iterable, size: int):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
