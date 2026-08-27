# Pillar: financial_health

Will this company still be here in eighteen months, and will the team you join still be
funded? For most candidates this outranks culture scores, and it is the pillar where free
public sources are strongest.

## Sources

1. **US filer**: `edgar.py --cik <cik> --facts --forms 10-K --items 1A,7 --limit 1`.
   `facts.series` gives six years of revenue, net income, operating income, cash and R&D
   straight from XBRL. `facts.revenue_trend` is the signal value, already computed.
2. **Indian listed**: `india_filings.py --name "<legal name>"` — `headline_metrics` carries
   EPS, PE, operating and net margin and ROE from BSE; `annual_reports` gives the PDFs.
3. **Private**: Form D filings (`edgar.py --forms D --no-items --limit 5`) date US raises.
   Otherwise press coverage of funding rounds, plus the honest admission that private
   financials are not public.
4. **Layoffs and restructuring**: cross-check with the `news` pillar rather than
   duplicating it, and cite the same sources.

## Signals owned by this pillar

- `revenue_trend` — `growing` / `flat` / `declining`. Take `edgar.py --facts`
  `revenue_trend.value` where it exists; for BSE-listed companies read three years of
  annual reports or the exchange numbers.
- `funding_months_ago` — private companies. Months since the most recent raise. A Form D
  `filing_date` is authoritative; a press date is `medium` at best.
- `layoff_events_24m` — only when the `news` pillar is not part of this stage. Never set a
  signal twice: merge.py rejects the second fragment that writes it.

## What a good claim looks like

- *"Revenue grew from $12.8bn (FY2020) to $19.3bn (FY2025), 8.6% compounded, with
  operating margin steady near 21%."*
- *"Cash fell from $3.4bn to $1.5bn over two years while net income held flat: the
  drawdown is buybacks, not losses (FY2023 20-F)."*
- Not: *"Financially strong."*

## Traps

- **Group versus subsidiary.** A US parent's numbers say little about the Indian entity
  you would join, and a subsidiary's filings say nothing about the parent's runway. Name
  the entity in every claim.
- **Currency and units.** Every XBRL point carries a `unit`. A 20-F may report USD while
  the Indian annual report reports INR crore. Never compare across without saying so.
- **Profitability is not survival.** A loss-making company with three years of cash is
  safer than a marginally profitable one with debt maturing next year. Where the filing
  discusses maturities, that is the decision-relevant fact.
- **Fiscal years differ.** Infosys FY2026 ends 31 March 2026; Alphabet FY2025 ends 31
  December 2025. Compare period ends, not labels.

## When to declare a gap

Unlisted, no public raise, no filings: `status: "gap"` with a reason naming what was
searched, and a `suggested_fallback` pointing at the parent entity when one exists.
