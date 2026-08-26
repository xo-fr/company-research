# Evidence fragment format

Every pillar subagent writes one file: `evidence/<pillar>.json`. `merge.py` assembles the
fragments, renumbers sources globally, and refuses anything with a dangling citation.
The machine-readable contract is `schemas/evidence.schema.json`.

## The shape you write

```json
{
  "sources": [
    { "id": "s1",
      "url": "https://www.sec.gov/Archives/edgar/data/1652044/.../goog-20251231.htm",
      "publisher": "SEC EDGAR",
      "title": "Alphabet Inc. 10-K, FY2025",
      "type": "filing",
      "published_at": "2026-02-05",
      "retrieved_at": "2026-08-27T09:04:00Z" }
  ],
  "pillars": {
    "overview": {
      "status": "ok",
      "entity_id": "us:0001652044",
      "claims": [
        { "id": "overview.0",
          "text": "Search advertising is 74% of revenue, which the company names as a concentration risk.",
          "source_ids": ["s1"],
          "confidence": "high",
          "as_of": "2026-02-05" }
      ],
      "gaps": []
    }
  },
  "signals": {
    "revenue_trend": { "value": "growing", "confidence": "high", "source_ids": ["s1"],
                       "note": "revenue up 13.9% year on year across six annual filings" }
  }
}
```

Source ids are **local to your fragment** (`s1`, `s2`, …). `merge.py` rewrites them, so
never reference an id you did not define yourself.

## Rules the merge enforces

| Rule | Why |
|---|---|
| Every `source_ids` entry exists in your `sources` | A citation pointing at nothing looks identical, on screen, to one that works |
| A pillar with zero claims has `status: "gap"` and ≥1 `gaps` entry | Empty and silent is indistinguishable from broken |
| `confidence: "none"` requires `value: null`, and vice versa | The dashboard counts scored signals; a guess with no evidence must not be counted |
| Signal values must normalise (see the registry) | `"many"` is not an integer |
| Claim ids are `<pillar>.<n>` | The narrative cites them; renumbering silently would break provenance |
| Narrative `claim_ids` must exist | Same reason |

Missing pillars and missing signals are both fine. Stage gating means most runs skip
several pillars, and most companies leave several signals unknowable.

## Claim quality

A good claim is one sentence, falsifiable, dated, and attributable:

- Good: *"The FY2025 20-F lists client concentration as a principal risk: the top ten
  clients are 19.4% of revenue."*
- Bad: *"The company seems financially healthy."* — not falsifiable, cites nothing.
- Bad: *"Employees say management is chaotic."* — who, when, how many?

Confidence: `high` = primary source states it directly; `medium` = credible secondary
report or a consistent pattern across sources; `low` = single anecdote, or an inference
you are flagging as such.

## Signal registry

Fourteen signals, six dimensions. Populate only the ones your pillar owns.

| id | dimension | raw value |
|---|---|---|
| `layoff_events_24m` | stability | int: distinct layoff events in 24 months |
| `funding_months_ago` | stability | int: months since last raise (private only) |
| `revenue_trend` | stability | `growing` / `flat` / `declining` |
| `role_repost_count_12m` | stability | int: times this role was reposted |
| `hiring_velocity_90d` | growth | float: net change in openings ÷ baseline |
| `headcount_trend_12m` | growth | float: fractional headcount change |
| `comp_percentile_vs_market` | comp | int 0–100 |
| `comp_transparency` | comp | bool: does the posting publish a range |
| `rating_current` | wlb | float 1–5 |
| `rating_trend_24m` | wlb | float: change in rating |
| `wlb_sentiment` | wlb | float −1..1 |
| `eng_output_signal` | learning | float 0–1 |
| `stack_currency` | learning | float 0–1 |
| `sponsorship_history_3y` | logistics | int: visa filings in 3 years |

## profile.yaml

```yaml
contact_email: you@example.com
resume_path: ~/docs/resume.pdf
base_market: IN
work_authorization:
  US: requires_sponsorship
  IN: citizen
seniority_band: senior
current_comp:
  currency: INR
  annual: 4200000
priorities:
  - stability
  - comp
  - learning
  - wlb
  - growth
```
