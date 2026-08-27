# AGENTS.md — company-research

For Codex and any other host that reads `AGENTS.md` rather than a skill directory. It is
the same playbook as `skills/company-research/SKILL.md`; the per-pillar detail lives in
`skills/company-research/references/*.md`, and you should read the one for the pillar you
are working on before you start it.

`$CR` below is the repo root.

## What this is

A job-seeker research tool. Input: a job posting URL, or a company name plus market.
Output: `dossier.html` — cited claims, 14 scored signals, and a verdict the reader can
re-weight. Sources are free and keyless. Nothing leaves the machine except source fetches.

**Division of labour:** if the output is identical every time given the same input, it
belongs in a script. Otherwise you do it. Scripts never call an LLM.

## Hard rules

1. **Never scrape LinkedIn**, reuse its cookies, or drive a headless browser session at it.
   If a fact is only there, the pillar is a `gap`.
2. **Never invent a number.** No compensation estimate for India, no inferred headcount, no
   undated rating. A gap with a fallback beats a confident guess.
3. **Every claim cites a source that exists** in the same fragment. `merge.py` enforces it.
4. **Never edit `evidence.json` by hand.** Fix the fragment and re-run `merge.py`.
5. **Respect robots.txt.** `--ignore-robots` exists for humans; do not use it.
6. **One signal, one fragment.** Two pillars writing the same signal is a merge error.

## Procedure

### 1. Preflight

Read `~/.company-research/profile.yaml`. If missing, ask these four things in one message,
then write it (schema in `references/evidence-schema.md`):

1. Contact email — SEC requires it in the User-Agent; it goes to sec.gov and nowhere else.
2. Base market (IN/US) and work authorisation per market.
3. Resume path (skippable).
4. Priority order over: stability, comp, wlb, learning, growth.

Then `export CR_CONTACT_EMAIL='<their email>'` for the session.

### 2. Inputs

Ask at most two questions: the posting URL (or company + market), and the stage
(`applying` / `interviewing` / `offer`). If both are already known, ask nothing.

### 3. Resolve

```bash
python $CR/scripts/resolve.py --jd-url "<url>" --market IN --pretty \
  --jd-out ~/.company-research/dossiers/<domain>-<YYYY-MM-DD>/jd.txt
```

Read `references/entity-resolution.md`. **Stop and ask the user** when `confidence < 0.7`,
when `candidates` is present, or when `employment_type` is `unknown` in the IN market.

### 4. Stage gate

| Stage | Pillars |
|---|---|
| `applying` | overview, news, financial_health, culture, hiring_trend |
| `interviewing` | overview, interview_prep, jd_gap, interviewers, culture, news |
| `offer` | compensation, financial_health, reviews, hiring_trend, culture |

### 5. Research each pillar

If your host supports subagents, run one per pillar in parallel; if not, work through them
in order. For each: read `references/<pillar>.md`, then write
`<dossier>/evidence/<pillar>.json` in the fragment format from
`references/evidence-schema.md`.

Useful invocations:

```bash
python $CR/scripts/edgar.py --cik 0001477333 --forms 10-K --items 1,1A --facts --limit 1
python $CR/scripts/india_filings.py --name "Infosys" --months 12
python $CR/scripts/h1b.py --employer "STRIPE, INC." --year 2025 --title "software engineer"
python $CR/scripts/snapshot.py --domain acme.com --role "Senior Backend Engineer"
python $CR/scripts/wayback_jobs.py --url "boards.greenhouse.io/acme*" --samples 14
python $CR/scripts/doctor.py --only gdelt        # when a source looks dead
```

Every script prints JSON to stdout, writes errors to stderr, and supports `--help`.

### 6. Merge, narrate, render, snapshot

```bash
python $CR/scripts/merge.py --dir <dossier> --stage <stage> --pretty
# then write <dossier>/evidence/narrative.json, then re-run merge.py
python $CR/scripts/render.py --evidence <dossier>/evidence.json --open
python $CR/scripts/snapshot.py --domain <domain>          # always, whatever the stage
```

`merge.py` fails loudly on dangling citations, contradictory signal confidence and unknown
pillar or signal names. Fix the fragment it names.

### 7. Present

Give the file path, then three sentences: the verdict, the strongest reason for it, and the
biggest open question. Do not restate the dossier in chat.

## Signals

Fourteen, six dimensions. Populate only those your pillar owns; missing signals are fine
and are reported as coverage rather than hidden.

| id | dimension | raw |
|---|---|---|
| `layoff_events_24m` | stability | int, distinct events |
| `funding_months_ago` | stability | int, private only |
| `revenue_trend` | stability | growing/flat/declining |
| `role_repost_count_12m` | stability | int |
| `hiring_velocity_90d` | growth | float, Δ openings / baseline |
| `headcount_trend_12m` | growth | float, fractional Δ |
| `comp_percentile_vs_market` | comp | int 0–100 |
| `comp_transparency` | comp | bool (leave unset where no employer in that market publishes) |
| `rating_current` | wlb | float 1–5 |
| `rating_trend_24m` | wlb | float Δ |
| `wlb_sentiment` | wlb | float −1..1 |
| `eng_output_signal` | learning | float 0–1 |
| `stack_currency` | learning | float 0–1 |
| `sponsorship_history_3y` | logistics | int (scored only if the profile needs sponsorship) |

## Failure handling

A dead source is a `gap` with a reason and a fallback, never an exception and never a zero.
`doctor.py` tells you whether the source or the company is the problem. Partial results
beat no results: a tool that raises on one dead source produces nothing on a Tuesday.
