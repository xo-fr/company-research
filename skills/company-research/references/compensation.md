# Pillar: compensation

What this role pays, from sources that publish numbers rather than estimate them.

The US and India are genuinely different problems here. Do not pretend otherwise in the
dossier: an honest gap is worth more than a fabricated range.

## US: DOL LCA disclosure data

Every H-1B, E-3 and H-1B1 petition requires a Labor Condition Application naming the
employer, the job title, the worksite and the wage. DOL publishes all of it quarterly.

```bash
python $CR/scripts/h1b.py --employer "STRIPE, INC." --year 2025 --title "software engineer" --pretty
python $CR/scripts/h1b.py --employer "ACME INC" --years 2023,2024,2025 --state CA --pretty
```

First run for a fiscal year downloads an 80–250 MB file and reports progress; later runs
read the cache. The output carries `wage_percentiles`, a per-title breakdown, and the
`sponsorship_history_3y` signal.

Read LCA wages as a **floor on base salary**: it is the minimum the employer commits to
pay, it excludes bonus and equity, and it covers only sponsored hires. Say this in the
claim itself, every time.

Also worth citing where present: pay ranges in the posting (US states increasingly
require them), and the company's own published levels or pay policy.

## India: a known permanent gap

There is no free structured source. AmbitionBox is Naukri-owned and scrape-hostile,
Indian postings rarely publish ranges, and no regulator collects salary by title. Do not
build or infer an estimate from cost-of-living ratios or US numbers.

Emit:

```json
{ "pillars": { "compensation": { "status": "gap", "gaps": [{
  "reason": "no free structured compensation source exists for the Indian market",
  "suggested_fallback": "web-search AmbitionBox/Levels.fyi/Glassdoor for this title and city, and treat the result as anecdote with a date"
}]}}}
```

Then, if the user wants a number, do that search openly as the agent and label it as
crowd-sourced anecdote in the narrative — not as a claim in this pillar.

## Signals owned by this pillar

- `comp_percentile_vs_market` (0–100) — only when you have both a market reference and a
  company figure from comparable sources (for example this employer's LCA median for the
  title versus the same title's median across peer employers in the same state and year).
  Never mix an LCA base wage with a total-compensation figure from elsewhere.
- `comp_transparency` (bool) — does the posting itself publish a range? Read it from the
  JD text, not from policy pages.
- `sponsorship_history_3y` — take it from `h1b.py`. It only enters the verdict when the
  user's profile says they need sponsorship; otherwise the dashboard shows it as
  information.

## What a good claim looks like

- *"Sixteen LCA filings for Software Engineer in FY2025 show base wages from $180k (p25)
  to $213k (p90), median $189k. These are committed minimum base pay, not total comp."*
- *"The posting publishes a range of ₹0 — no range is given, which is the Indian norm and
  not a company-specific signal."*
- Not: *"Pays market rate."*

## Traps

- **Legal name, not brand.** LCA filings say "STRIPE, INC.", not "Stripe". The script
  matches on a normalised prefix; check `matched_employer_names` in the output to confirm
  it did not sweep in a same-named unrelated filer.
- **Seniority.** "Software Engineer" at one company spans three levels. Where the data has
  titles like "Software Engineer L5", use them, and say which level a number describes.
- **Location.** A New York wage is not a Bengaluru wage and not an Austin wage. Filter by
  `--state`, and name the worksite in the claim.
- **Sponsored hires only.** LCA data describes a subset of employees who may be paid
  differently from the median hire. It is a floor and a sample, and both caveats matter.
