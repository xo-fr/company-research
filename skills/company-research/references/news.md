# Pillar: news

What has happened to this company in the last 18 months, weighted towards things that
change what it is like to work there: layoffs, funding, leadership churn, regulatory
trouble, acquisitions.

## Sources

1. **GDELT** — global news index, keyless:
   `https://api.gdeltproject.org/api/v2/doc/doc?query="<brand>"&mode=artlist&maxrecords=50&format=json&sort=datedesc`
   Add `&startdatetime=YYYYMMDDHHMMSS` to window it. Non-English coverage is good, which
   matters for Indian companies.
2. **Hacker News (Algolia)** — `https://hn.algolia.com/api/v1/search_by_date?query=<brand>`
   for what engineers said at the time, including the comments on a layoff thread.
3. **Company announcements** — `india_filings.py` (BSE Reg 30 filings are legally
   required and dated) or `edgar.py --forms 8-K` for US filers. An 8-K Item 5.02 is a
   named executive leaving.
4. Your own web search, for anything the above missed.

Google News RSS is deliberately not used: `news.google.com/robots.txt` disallows it for
every agent, and this tool respects that.

## What a good claim looks like

- *"Cut 6% of staff in March 2026, second reduction in 14 months (Reuters, 2026-03-11)."*
- *"CFO departed after nine months; the 8-K gives no reason (2026-05-02)."*
- Not: *"Has faced some challenges recently."*

Prefer the primary filing over the article about the filing. Where both exist, cite both.

## Signals owned by this pillar

- `layoff_events_24m` — count **distinct events**, not articles. Five outlets covering
  one layoff is one event. Rolling "performance-based" cuts reported repeatedly across
  months are separate events only if the company confirmed separate actions.
- `funding_months_ago` — if the news pillar is where the raise turned up, set it here and
  say so in the note, so overview does not double-count.

## Traps

- **Recency illusion.** GDELT indexes press releases too. A wire release republished by
  40 sites is one source, not 40.
- **Layoff aggregators** are useful for dates and unreliable for numbers. Cite the
  company statement where one exists; otherwise mark the count `medium`.
- **Old news reads as current.** Every claim needs `as_of`. An 18-month-old crisis that
  was resolved is a different fact from an unresolved one.
- Sentiment on HN skews negative and skews technical. Use it for specifics
  ("their API deprecation broke us"), not for mood.
