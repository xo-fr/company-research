"""Render evidence.json into a single self-contained dossier.

    python scripts/render.py --evidence <dossier>/evidence.json
    python scripts/render.py --evidence e.json --out /tmp/dossier.html --open

Injects the evidence document and the signal registry into ``templates/dossier.html`` as
two JSON script blocks. No CDN, no build step, no network at view time: the verdict is
computed in the reader's browser from the evidence sitting in the same file, which is
what lets them drag a priority slider and watch the score move.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "dossier.html"

PILLAR_LABELS = {
    "overview": "What the company does",
    "news": "Recent news",
    "hiring_trend": "Hiring trend",
    "culture": "Culture",
    "compensation": "Compensation",
    "reviews": "Employee reviews",
    "interview_prep": "Interview process",
    "financial_health": "Financial health",
    "interviewers": "Your interviewers",
    "jd_gap": "You vs the job description",
}


def registry() -> dict:
    return {
        "signals": common.SIGNALS,
        "dimensions": common.DIMENSIONS,
        "dimension_labels": common.DIMENSION_LABELS,
        "pillar_order": common.PILLARS,
        "pillar_labels": PILLAR_LABELS,
        "spec_version": common.SPEC_VERSION,
    }


def _embed(payload) -> str:
    """JSON safe to sit inside a <script> element.

    Escapes the sequence that would close the script tag early, and the two Unicode
    line separators that are legal in JSON but terminate a JavaScript string literal.
    """
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render(evidence: dict, template_path: Path = TEMPLATE) -> str:
    entity = evidence.get("entity", {})
    query = evidence.get("query", {})
    title_bits = [entity.get("brand") or "Company dossier"]
    if query.get("role"):
        title_bits.append(query["role"])
    html = template_path.read_text(encoding="utf-8")
    return (
        html.replace("__TITLE__", " · ".join(title_bits))
        .replace("__EVIDENCE_JSON__", _embed(evidence))
        .replace("__SIGNALS_JSON__", _embed(registry()))
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--out", help="default: dossier.html beside the evidence file")
    parser.add_argument("--template", default=str(TEMPLATE))
    parser.add_argument("--open", action="store_true", help="open the result in a browser")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    evidence_path = Path(args.evidence).expanduser()
    if not evidence_path.exists():
        common.fail(f"render.py: no evidence file at {evidence_path}")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    out_path = Path(args.out).expanduser() if args.out else evidence_path.parent / "dossier.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(evidence, Path(args.template).expanduser()), encoding="utf-8")

    if args.open:
        webbrowser.open(out_path.resolve().as_uri())

    signals = evidence.get("signals", {})
    scored = [s for s in signals.values() if s.get("confidence") != "none"]
    common.emit(
        {
            "status": "ok",
            "written": str(out_path),
            "url": out_path.resolve().as_uri(),
            "bytes": out_path.stat().st_size,
            "signals_scored": len(scored),
            "signals_total": len(common.SIGNALS),
        },
        args.pretty,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
