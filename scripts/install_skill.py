"""Register the company-research skill with a local agent host.

    python scripts/install_skill.py            # link into every host found
    python scripts/install_skill.py --host claude --copy
    python scripts/install_skill.py --uninstall

Links (or copies, on hosts where symlinks are awkward) ``skills/company-research`` into
the host's skills directory. No remote script execution, no shell piping: the install
path is ``git clone`` then this file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC = REPO_ROOT / "skills" / "company-research"

HOSTS = {
    # host id -> (skills dir, human name)
    "claude": (Path.home() / ".claude" / "skills", "Claude Code"),
    "claude-project": (REPO_ROOT / ".claude" / "skills", "Claude Code (this project)"),
    "codex": (Path.home() / ".codex" / "skills", "Codex"),
}


def _link(src: Path, dest: Path, copy: bool) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() and Path(os.readlink(dest)).resolve() == src.resolve():
            return "already linked"
        return "exists (left alone; pass --force to replace)"
    if not copy:
        try:
            dest.symlink_to(src, target_is_directory=True)
            return "linked"
        except OSError:
            # Windows without Developer Mode: fall through to a copy.
            pass
    shutil.copytree(src, dest)
    return "copied"


def install(host_ids, copy: bool, force: bool) -> list[dict]:
    results = []
    for hid in host_ids:
        skills_dir, name = HOSTS[hid]
        dest = skills_dir / "company-research"
        if force and (dest.exists() or dest.is_symlink()):
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            else:
                shutil.rmtree(dest)
        exists_host = skills_dir.parent.exists()
        action = _link(SKILL_SRC, dest, copy) if (exists_host or hid == "claude-project") else "skipped (host not installed)"
        results.append({"host": hid, "name": name, "path": str(dest), "action": action})
    return results


def uninstall(host_ids) -> list[dict]:
    results = []
    for hid in host_ids:
        skills_dir, name = HOSTS[hid]
        dest = skills_dir / "company-research"
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
            action = "unlinked"
        elif dest.is_dir():
            shutil.rmtree(dest)
            action = "removed copy"
        else:
            action = "not installed"
        results.append({"host": hid, "name": name, "path": str(dest), "action": action})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="all", help="claude | claude-project | codex | all")
    parser.add_argument("--copy", action="store_true", help="copy instead of symlinking")
    parser.add_argument("--force", action="store_true", help="replace an existing install")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    if not SKILL_SRC.is_dir():
        common.fail(f"skill source not found: {SKILL_SRC}")

    host_ids = list(HOSTS) if args.host == "all" else [h.strip() for h in args.host.split(",")]
    unknown = [h for h in host_ids if h not in HOSTS]
    if unknown:
        common.fail(f"unknown host(s): {', '.join(unknown)}. Known: {', '.join(HOSTS)}")

    results = uninstall(host_ids) if args.uninstall else install(host_ids, args.copy, args.force)
    payload = {"source": str(SKILL_SRC), "results": results}
    common.emit(payload, args.pretty)
    for r in results:
        print(f"{r['name']:<28} {r['action']:<38} {r['path']}", file=sys.stderr)
    if not args.uninstall:
        print(
            "\nNext: set your contact email once (SEC requires it in the User-Agent):\n"
            "  export CR_CONTACT_EMAIL='you@example.com'\n"
            "Then ask your agent: \"research this company for me: <job posting URL>\"",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
