# Pillar: reviews

Aggregated employee sentiment — reported as a **trend**, never as a snapshot.

A 3.6 rating means nothing on its own. A 4.2 that fell to 3.6 across two years means a
great deal, and so does a 3.4 that climbed to 3.6. Report the movement or report nothing.

## Sources

Glassdoor, AmbitionBox, Indeed and Blind do not offer free structured access and are
hostile to automated collection. Do not scrape them, and do not route around a block.

What is available:

1. **Your own web search**, as the agent, for review-site pages and their headline
   numbers. Quote the number, the site, and the date you read it.
2. **Archived review pages** via `wayback_jobs.py --url "glassdoor.com/Reviews/<slug>*"`
   used purely to date the rating history — the CDX index and archived captures are public
   record. Rate limits apply; keep samples small.
3. **Hacker News and Reddit threads** for specifics that ratings cannot carry ("the
   rating dropped after the 2025 return-to-office mandate").
4. **The company's own attrition disclosure.** Indian IT companies publish quarterly
   attrition in results filings — `india_filings.py` finds those. It is the most rigorous
   employee-satisfaction number available anywhere, because it is audited.

## Signals owned by this pillar

- `rating_current` (1–5) — the current headline rating, with the platform and read date in
  the note. If you have ratings from two platforms, use the one with more reviews and
  mention the other in a claim.
- `rating_trend_24m` (float delta) — the change over roughly two years. Only from dated
  evidence: an archived page, a cited article, or a company disclosure. Never estimated.

If you cannot date a rating, set `rating_trend_24m` to confidence `none` and say why.

## What a good claim looks like

- *"Attrition (voluntary, trailing twelve months) fell from 17.3% to 13.1% across FY2025
  per the company's own quarterly results — a real improvement in retention."*
- *"Glassdoor showed 3.6 (4,120 reviews) when read on 2026-08-27; an archived capture from
  2024-09 showed 4.0 (2,880 reviews)."*
- Not: *"Employees rate it 3.6 out of 5."* — no date, no direction, no base.

## Traps

- **Review-bombing and recruiting drives** both move ratings without anything changing
  inside. A jump of more than 0.3 in a quarter usually means a campaign, not a culture.
- **Volume matters.** A 4.6 from 22 reviews at a 3,000-person company is noise.
- **Country splits.** Global ratings hide a site. Where the platform separates India from
  the US, use the split that matches the role.
- **Never present a scraped block of review text as a claim.** Quote at most a phrase, and
  attribute it.
