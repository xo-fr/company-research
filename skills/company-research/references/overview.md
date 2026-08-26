# Pillar: overview

What this company does, from the point of view of someone deciding whether to join it —
not the marketing version. Two questions: where does the money come from, and what does
the company itself say could go wrong.

## Sources, in priority order

1. **The company's own annual filing.** US filer: `edgar.py --items 1,1A --forms 10-K`.
   Foreign private issuer (Infosys, Wipro, HDFC): `--forms 20-F --items 3D,4` — 20-F
   numbering differs, and the script falls back to the parent Item automatically.
   Item 1A / Item 3.D is the company enumerating its own problems under legal
   obligation. Nothing else in this tool is that candid.
2. **Indian listed**: `india_filings.py --name "<legal name>"` for announcements,
   annual report PDFs and the exchange's headline ratios.
3. **The company's own site**: product pages, engineering blog, customers page.
4. Wikidata facts already in the resolve output: inception, employee count, parent,
   subsidiaries, industry.

```bash
python $CR/scripts/edgar.py --cik 0001652044 --forms 10-K --items 1,1A --limit 1 --facts --pretty
python $CR/scripts/india_filings.py --name "Infosys" --months 12 --pretty
```

## What a good claim looks like

- *"Item 1A names dependence on a single cloud provider as a principal risk; the
  contract renews in 2027."* — dated, sourced, decision-relevant.
- *"Revenue grew 13.9% year on year across the last six annual filings, from XBRL data."*
- Not: *"A leading provider of innovative solutions."* That is their words, not a fact.

Read Item 1A for what is *new* this year. Risk sections are copied forward, so a newly
added paragraph is the company telling you what changed.

## Signals owned by this pillar

- `revenue_trend` — take it straight from `edgar.py --facts` → `revenue_trend.value`.
  Confidence `high` with three or more annual points, `medium` with two.
- `funding_months_ago` — private companies only. Form D filings (`--forms D`) date the
  last raise; a press-reported round is `medium` confidence at best.

## Traps

- The brand's blog post about a product is not evidence the product makes money.
- A 10-K describes the *filer*. If the role is at an Indian subsidiary, say which entity
  each claim is about — the parent's 40% margin is not the GCC's reality.
- `--max-chars` truncates long Items by design. If you need more of one section, raise it
  for that call rather than dumping the whole filing into context.

## When to declare a gap

Unlisted private company with no filings anywhere: `status: "partial"` on whatever the
company site and news give you, with a gap entry naming what is missing. Only use
`status: "gap"` when you have no citable claim at all.
