"""Assemble pillar fragments into one validated evidence file.

    python scripts/merge.py --dir ~/.company-research/dossiers/acme.com-2026-08-26
    python scripts/merge.py --dir <dossier> --narrative /tmp/narrative.json

Reads ``<dir>/evidence/*.json``, validates each fragment against
``schemas/evidence.schema.json``, deduplicates sources by URL, rewrites every
``source_ids`` reference to the canonical ids, enforces the integrity rules, and writes
``<dir>/evidence.json``.

Exits non-zero listing every dangling reference it found. That strictness is the point:
a citation that points at nothing is indistinguishable, on screen, from one that does.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "evidence.schema.json"


# --------------------------------------------------------------- schema validator


def _type_ok(value, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate(instance, schema: dict, root: dict, path: str = "$") -> list[str]:
    """A deliberately small JSON Schema subset: type, required, properties,
    additionalProperties, enum, pattern, items, min/max, $ref into $defs.

    Enough to enforce this project's contract with no third-party dependency, which is
    what keeps ``pip install`` optional for the whole tool.
    """
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return [f"{path}: unsupported $ref {ref}"]
        target = root
        for part in ref[2:].split("/"):
            target = target.get(part, {})
        return validate(instance, target, root, path)

    if "type" in schema:
        expected = schema["type"]
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, opt) for opt in options):
            return [f"{path}: expected {'/'.join(options)}, got {type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not one of {schema['enum']}")

    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} above maximum {schema['maximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate(value, properties[key], root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(validate(value, schema["additionalProperties"], root, f"{path}.{key}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, item_schema, root, f"{path}[{index}]"))
    return errors


# ------------------------------------------------------------------------- merging


def _canonical_url(url: str) -> str:
    """Same document, different query string, is still the same source."""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parts.query)
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref"}
    query = [(k, v) for k, v in query if k.lower() not in drop]
    path = parts.path.rstrip("/") or "/"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, urllib.parse.urlencode(query), ""))


class SourceTable:
    """Deduplicates sources across fragments and hands out stable ids."""

    def __init__(self) -> None:
        self.by_url: dict[str, dict] = {}
        self.order: list[str] = []

    def add(self, source: dict) -> str:
        key = _canonical_url(source.get("url", ""))
        if key in self.by_url:
            existing = self.by_url[key]
            for field in ("publisher", "published_at", "title", "type"):
                if not existing.get(field) and source.get(field):
                    existing[field] = source[field]
            return existing["id"]
        record = dict(source)
        record["id"] = f"s{len(self.order) + 1}"
        self.by_url[key] = record
        self.order.append(key)
        return record["id"]

    def as_list(self) -> list[dict]:
        return [self.by_url[key] for key in self.order]


def load_fragments(evidence_dir: Path) -> list[tuple[Path, dict]]:
    fragments = []
    for path in sorted(evidence_dir.glob("*.json")):
        if path.name == "narrative.json":
            continue
        try:
            fragments.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"merge.py: {path.name} is not valid JSON: {exc}")
    return fragments


def merge(dossier_dir: Path, narrative_path: Path | None, stage: str | None) -> tuple[dict, list[str]]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    evidence_dir = dossier_dir / "evidence"
    if not evidence_dir.is_dir():
        raise SystemExit(f"merge.py: no evidence directory at {evidence_dir}")

    fragments = load_fragments(evidence_dir)
    if not fragments:
        raise SystemExit(f"merge.py: no fragments in {evidence_dir}")

    errors: list[str] = []
    sources = SourceTable()
    pillars: dict[str, dict] = {}
    signals: dict[str, dict] = {}
    entity: dict = {}
    query: dict = {}
    meta: dict = {}
    profile: dict = {}

    for path, fragment in fragments:
        # Fragments carry a subset of the full document; validate the parts present.
        for key, subschema in (
            ("entity", schema["$defs"]["entity"]),
            ("query", schema["$defs"]["query"]),
        ):
            if key in fragment:
                errors += [f"{path.name}: {e}" for e in validate(fragment[key], subschema, schema, f"${key}")]
        for source in fragment.get("sources", []):
            errors += [f"{path.name}: {e}" for e in validate(source, schema["$defs"]["source"], schema, "$source")]
        for name, pillar in (fragment.get("pillars") or {}).items():
            if name not in common.PILLARS:
                errors.append(f"{path.name}: unknown pillar {name!r}")
                continue
            errors += [f"{path.name}: {e}" for e in validate(pillar, schema["$defs"]["pillar"], schema, f"$pillars.{name}")]
        for sid, signal in (fragment.get("signals") or {}).items():
            if sid not in common.SIGNALS:
                errors.append(f"{path.name}: unknown signal {sid!r} (see BUILD-SPEC section 6)")
                continue
            errors += [f"{path.name}: {e}" for e in validate(signal, schema["$defs"]["signal"], schema, f"$signals.{sid}")]

        # Sources are renumbered globally, so every reference inside this fragment has
        # to be rewritten through the fragment's own local id map.
        local_map: dict[str, str] = {}
        for source in fragment.get("sources", []):
            local_id = source.get("id")
            new_id = sources.add(source)
            if local_id:
                local_map[local_id] = new_id

        def remap(ids, where: str) -> list[str]:
            out = []
            for ref in ids or []:
                if ref in local_map:
                    out.append(local_map[ref])
                elif ref in {s["id"] for s in sources.as_list()}:
                    out.append(ref)
                else:
                    errors.append(f"{path.name}: {where} cites unknown source id {ref!r}")
            return out

        for name, pillar in (fragment.get("pillars") or {}).items():
            merged = pillars.setdefault(name, {"status": pillar.get("status", "gap"), "claims": [], "gaps": []})
            if pillar.get("entity_id"):
                merged["entity_id"] = pillar["entity_id"]
            # Two fragments touching one pillar: the more complete status wins.
            rank = {"gap": 0, "partial": 1, "ok": 2}
            if rank.get(pillar.get("status", "gap"), 0) > rank.get(merged["status"], 0):
                merged["status"] = pillar["status"]
            for claim in pillar.get("claims", []):
                claim = dict(claim)
                claim["source_ids"] = remap(claim.get("source_ids"), f"claim {claim.get('id')}")
                claim["id"] = _claim_id(name, claim.get("id"), merged["claims"])
                merged["claims"].append(claim)
            merged["gaps"].extend(pillar.get("gaps", []))

        for sid, signal in (fragment.get("signals") or {}).items():
            if sid not in common.SIGNALS:
                continue
            record = dict(signal)
            record["source_ids"] = remap(record.get("source_ids"), f"signal {sid}")
            if sid in signals and signals[sid].get("confidence") != "none":
                errors.append(f"{path.name}: signal {sid!r} already set by another fragment")
                continue
            signals[sid] = record

        entity = entity or fragment.get("entity", {})
        query = query or fragment.get("query", {})
        profile = profile or fragment.get("profile", {})
        meta.update(fragment.get("meta", {}))

    narrative = None
    narrative_file = narrative_path or (evidence_dir / "narrative.json")
    if narrative_file and Path(narrative_file).exists():
        narrative = json.loads(Path(narrative_file).read_text(encoding="utf-8"))
        narrative = narrative.get("narrative", narrative)
        errors += [f"narrative: {e}" for e in validate(narrative, schema["$defs"]["narrative"], schema, "$narrative")]

    document = {
        "meta": {
            "generated_at": common.iso_now(),
            "stage": stage or meta.get("stage") or "applying",
            "spec_version": common.SPEC_VERSION,
            "generator": f"company-research/{common.VERSION}",
        },
        "entity": entity,
        "query": query,
        "sources": sources.as_list(),
        "pillars": pillars,
        "signals": signals,
    }
    if narrative:
        document["narrative"] = narrative
    if profile:
        document["profile"] = profile

    errors += enforce_rules(document)
    errors += validate(document, schema, schema)
    return document, errors


def _claim_id(pillar: str, given: str | None, existing: list[dict]) -> str:
    """Claim ids are ``<pillar>.<n>``. Rewrite anything else so citations stay legible."""
    taken = {c["id"] for c in existing}
    if given and given.startswith(f"{pillar}.") and given not in taken:
        return given
    index = len(existing)
    while f"{pillar}.{index}" in taken:
        index += 1
    return f"{pillar}.{index}"


def enforce_rules(document: dict) -> list[str]:
    """The rules from BUILD-SPEC section 5 that a schema cannot express."""
    errors: list[str] = []
    known_sources = {s["id"] for s in document.get("sources", [])}
    claim_ids: set[str] = set()

    for name, pillar in document.get("pillars", {}).items():
        claims = pillar.get("claims", [])
        for claim in claims:
            claim_ids.add(claim["id"])
            for ref in claim.get("source_ids", []):
                if ref not in known_sources:
                    errors.append(f"pillar {name}: claim {claim['id']} cites missing source {ref}")
            if not claim.get("source_ids"):
                errors.append(f"pillar {name}: claim {claim['id']} has no sources")
        if not claims:
            if pillar.get("status") != "gap":
                errors.append(f"pillar {name}: has no claims, so status must be 'gap' (is {pillar.get('status')!r})")
            if not pillar.get("gaps"):
                errors.append(f"pillar {name}: has no claims and no gaps explaining why")

    for sid, signal in document.get("signals", {}).items():
        for ref in signal.get("source_ids", []):
            if ref not in known_sources:
                errors.append(f"signal {sid}: cites missing source {ref}")
        if signal.get("confidence") == "none" and signal.get("value") is not None:
            errors.append(f"signal {sid}: confidence 'none' requires value null")
        if signal.get("confidence") != "none":
            if signal.get("value") is None:
                errors.append(f"signal {sid}: null value needs confidence 'none'")
            elif common.normalize_signal(sid, signal["value"]) is None:
                errors.append(
                    f"signal {sid}: value {signal['value']!r} cannot be normalised "
                    f"(expected {common.SIGNALS[sid]['raw']})"
                )

    narrative = document.get("narrative") or {}
    for section in ("strengths", "concerns"):
        for index, point in enumerate(narrative.get(section, [])):
            for ref in point.get("claim_ids", []):
                if ref not in claim_ids:
                    errors.append(f"narrative.{section}[{index}]: cites unknown claim {ref}")
    return errors


def coverage(document: dict) -> dict:
    counted = [s for s in document.get("signals", {}).values() if s.get("confidence") != "none"]
    return {
        "signals_present": len(counted),
        "signals_total": len(common.SIGNALS),
        "pillars_present": len(document.get("pillars", {})),
        "claims": sum(len(p.get("claims", [])) for p in document.get("pillars", {}).values()),
        "sources": len(document.get("sources", [])),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, help="dossier directory containing evidence/")
    parser.add_argument("--narrative", help="narrative JSON to fold in (default: evidence/narrative.json)")
    parser.add_argument("--stage", choices=list(common.STAGE_PILLARS), help="override meta.stage")
    parser.add_argument("--out", help="output path (default: <dir>/evidence.json)")
    parser.add_argument("--check", action="store_true", help="validate only, write nothing")
    common.add_common_args(parser)
    args = parser.parse_args(argv)

    dossier_dir = Path(args.dir).expanduser()
    document, errors = merge(
        dossier_dir, Path(args.narrative).expanduser() if args.narrative else None, args.stage
    )

    errors = list(dict.fromkeys(errors))  # the same fault can surface per-fragment and again on the whole
    if errors:
        print("merge.py: evidence failed validation\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s). Nothing written.", file=sys.stderr)
        return 1

    out_path = Path(args.out).expanduser() if args.out else dossier_dir / "evidence.json"
    if not args.check:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

    common.emit(
        {
            "status": "ok",
            "written": None if args.check else str(out_path),
            "coverage": coverage(document),
            "pillars": {k: v.get("status") for k, v in document["pillars"].items()},
        },
        args.pretty,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
