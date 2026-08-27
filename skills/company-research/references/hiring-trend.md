# Pillar: hiring_trend

Is this company growing, holding, or quietly shrinking — and has this particular role been
posted before?

## Sources

1. **Today's board**: `snapshot.py --domain <domain>` returns the current open roles and
   appends them to the local history. It resolves the company's ATS (Greenhouse, Lever,
   Ashby, SmartRecruiters, Workable, Recruitee) and reads its public API, so titles,
   locations and ids are exact rather than scraped.
2. **History from before you started looking**: `wayback_jobs.py --domain <domain>`, or
   better `--url "boards.greenhouse.io/<slug>*"` once snapshot.py has told you the slug.
3. **Local history**: `snapshot.py --domain <domain> --report-only --role "<title>"` once
   two or more snapshots exist. This is where `role_repost_count_12m` comes from.

```bash
python $CR/scripts/snapshot.py --domain acme.com --role "Senior Backend Engineer" --pretty
python $CR/scripts/wayback_jobs.py --url "boards.greenhouse.io/acme*" --samples 14 --pretty
```

## Signals owned by this pillar

- `hiring_velocity_90d` — net change in openings over ~90 days divided by the baseline
  count. Prefer local snapshots; fall back to the Wayback series, and drop confidence to
  `low` when that series came from the URL census rather than a board page.
- `role_repost_count_12m` — how many separate times this role appeared. One is normal.
  Three or more in a year means the team lost the person, or never made an offer, or the
  manager is the problem. It is one of the highest-signal facts in the dossier.
- `headcount_trend_12m` — only from a *reported* headcount figure (filing, annual report,
  credible press). Openings are not headcount; never infer one from the other.

## What a good claim looks like

- *"Open roles fell from 117 (June 2024) to 60 (December 2024) and rose to 166 by March
  2025: a freeze followed by a large restart."*
- *"This role has been posted three times since January 2026 in Bengaluru, disappearing
  and returning twice."*
- Not: *"They are hiring a lot."*

## Traps

- **JavaScript boards.** A careers page that renders client-side archives as an empty
  shell, so old captures legitimately show zero. `snapshot.py` reports its `method`; when
  that is `html` and the count is 0, the number is *missing*, not zero. Say so.
- **Crawl intensity is not hiring volume.** A month with more archive captures is not a
  month with more jobs. Only compare counts read from a board index page.
- **Aggregate counts hide teams.** Four hundred openings company-wide with none on the
  team you would join is a shrinking team inside a growing company. Filter by title.
- **A brand-new board.** If the ATS slug changed recently, history before the change lives
  at the old URL. Check both before concluding hiring collapsed.
