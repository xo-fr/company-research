---
name: company-research
description: Research a company before applying, interviewing, or accepting an offer. Produces a cited HTML dossier with a re-weightable verdict from free, keyless sources (SEC EDGAR, BSE, Wikidata, GDELT, DOL H-1B, Wayback). Use when the user shares a job posting, asks whether to apply somewhere, prepares for an interview, or is weighing an offer.
---

# Company research

Turn a job posting into a cited dossier. You do the judgement; the scripts in `scripts/`
do the deterministic work. Never put an LLM call inside a script, and never scrape
LinkedIn — that exclusion is permanent, not a limitation to work around.

`$CR` below means the repo root (the directory containing `scripts/`). Run every script
with `python $CR/scripts/<name>.py --help` if you need its exact flags.

## 1. Preflight

Read `~/.company-research/profile.yaml`. If it is missing, ask these four questions in
one message, then write the file (format in `references/evidence-schema.md`):

1. Contact email — SEC requires it in the User-Agent; it is sent to sec.gov and nowhere else.
2. Base market (IN / US) and work authorisation for each market you may apply in.
3. Resume path (skippable).
4. Priority order across: stability, comp, wlb, learning, growth.

Then export it for the session: `export CR_CONTACT_EMAIL='<their email>'`.

## 2. Inputs

Ask at most two questions before starting work:

- The job posting URL (strongly preferred), or company name + market.
- The stage: `applying`, `interviewing`, or `offer`.

If the user already gave both, ask nothing and start.

## 3. Resolve the entity

```bash
python $CR/scripts/resolve.py --jd-url "<url>" --market IN --pretty \
  --jd-out ~/.company-research/dossiers/<domain>-<YYYY-MM-DD>/jd.txt
```

Load `references/entity-resolution.md` before interpreting the result. Stop and ask the
user when `confidence < 0.7`, when `candidates` is present, or when `employment_type` is
`unknown` in the IN market. Do not pick between candidates yourself — every pillar
inherits the mistake.

## 4. Stage gate

| Stage | Pillars |
|---|---|
| `applying` | overview, news, financial_health, culture, hiring_trend |
| `interviewing` | overview, interview_prep, jd_gap, interviewers, culture, news |
| `offer` | compensation, financial_health, reviews, hiring_trend, culture |

## 5. Fan out

Create `~/.company-research/dossiers/<domain>-<YYYY-MM-DD>/evidence/`, then spawn **one
subagent per pillar**, in parallel. Give each exactly this:

- its pillar name and the `entity_id` to bind claims to;
- the resolved entity JSON and the JD text path;
- the output path `evidence/<pillar>.json`;
- the instruction: *read `references/evidence-schema.md` and `references/<pillar>.md`
  first, then research only that pillar and write the fragment.*

Rules every subagent inherits:

- Every claim cites at least one source id that exists in its own `sources` array.
- A pillar with no claims is `status: "gap"` with at least one `gaps` entry explaining
  why. An honest gap beats a padded claim.
- Populate only the signals listed in that pillar's reference file.
- Prefer primary sources (filings, the company's own careers page) over commentary.
- Date every claim. A 2019 interview report is worse than no interview report.

## 6. Merge

```bash
python $CR/scripts/merge.py --dir <dossier-dir> --stage <stage> --pretty
```

It fails loudly on dangling citations, contradictory signal confidence, and unknown
pillar or signal names. Fix the fragment it names and re-run; do not hand-edit
`evidence.json`.

## 7. Narrative

Only after the merge passes, write `evidence/narrative.json`:

```json
{ "narrative": {
  "summary": "3-5 sentences, every factual assertion traceable to a claim id",
  "strengths": [{ "text": "...", "claim_ids": ["overview.0"] }],
  "concerns":  [{ "text": "...", "claim_ids": ["news.2"] }],
  "questions_to_ask": ["..."] } }
```

Write the concerns you would want to read if it were your offer. Then re-run `merge.py`
(it folds the narrative in and re-validates the claim ids).

## 8. Render

```bash
python $CR/scripts/render.py --evidence <dossier-dir>/evidence.json --open
```

## 9. Snapshot

Always, regardless of stage — this is what makes the *next* run better:

```bash
python $CR/scripts/snapshot.py --domain <domain>
```

## 10. Present

Give the user the dossier path, then three sentences: the verdict, the strongest reason
for it, and the biggest open question. Do not restate the dossier in chat — it is a
document, and they are about to open it.

## Health check

If a pillar comes back empty and you suspect the source rather than the company:

```bash
python $CR/scripts/doctor.py
```

## References

Load on demand, one per pillar: `entity-resolution.md`, `overview.md`, `news.md`,
`hiring-trend.md`, `culture.md`, `compensation.md`, `reviews.md`, `interview-prep.md`,
`financial-health.md`, `interviewers.md`, `jd-gap.md`, `evidence-schema.md`.
