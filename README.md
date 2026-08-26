# company-research

Research a company before you apply, interview, or sign — inside the Claude Code or Codex
subscription you already pay for. No server, no MCP daemon, no API keys, no hosting.

You give your agent a job posting URL. You get back a single self-contained HTML dossier:
cited claims from keyless public sources, per-dimension scores, and a verdict you can
re-weight against your own priorities with a slider.

> Status: in active build against [`docs/BUILD-SPEC.md`](docs/BUILD-SPEC.md).

## Install

```bash
git clone https://github.com/xo-fr/company-research.git
cd company-research
python scripts/install_skill.py          # links the skill into Claude Code / Codex
export CR_CONTACT_EMAIL='you@example.com'  # SEC requires a contact in the User-Agent
python scripts/doctor.py                 # confirms every source is reachable
```

No remote script execution, no `curl | sh`. Python 3.10+, standard library only
(`httpx` is used when present and not required).

## Use

Paste this to your agent:

> Research this company for me before I apply: `<job posting URL>`

## What it does not do

- **No LinkedIn scraping**, cookie handling, or headless-browser session reuse. Ever.
- No database, no MCP server, no web server, no telemetry. Files on your disk.
- No LLM calls inside any script. Scripts are deterministic; judgement is the agent's job.
- No India compensation estimator — no free structured source exists, so the dossier
  marks it an honest `gap` instead of guessing.

## Licence

MIT.
