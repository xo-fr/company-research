# Pillar: culture

What it is actually like inside, assembled from what people say in public — with the
selection bias stated rather than ignored.

## Sources

1. **Engineering blog and open source.** What a company chooses to write about is what it
   values. `https://api.github.com/orgs/<org>` and
   `https://api.github.com/orgs/<org>/repos?sort=pushed&per_page=20` show whether the
   public repos are alive or abandoned.
2. **Hacker News**: `https://hn.algolia.com/api/v1/search?query=<brand>&tags=comment`, and
   `search_by_date` for recent threads. Employees and ex-employees speak there in specifics.
3. **Conference talks, papers, podcasts** by named engineers who work there.
4. **The company's own engineering handbook or values page**, where one exists — read it
   against the JD, not on its own.
5. Reddit and forum threads via web search, treated explicitly as anecdote.

## Signals owned by this pillar

- `wlb_sentiment` (−1..1) — only with at least three independent, dated, first-hand
  accounts. Two angry comments are two comments, not sentiment. With fewer than three,
  leave it unset and record a gap saying so.
- `eng_output_signal` (0..1) — public engineering output that is *current*: commits in
  active repos, talks, papers, RFCs. 0.2 for a dead GitHub org; 0.8 for a company whose
  engineers publish continuously.
- `stack_currency` (0..1) — how current the stack is, from the JD, the blog and the repos.
  Judge against the role, not fashion: COBOL at a bank is not automatically 0.1, but a
  2013 stack advertised as cutting edge is.

## What a good claim looks like

- *"Four of the five most active public repositories have had no commit in over a year,
  while the engineering blog posts monthly about the same three projects."*
- *"Three ex-employees on HN in 2026 describe the same on-call rotation problem
  (2026-04-11, 2026-05-02, 2026-06-30); one posts under a real name."*
- Not: *"Culture seems mixed."*

## Traps

- **Selection bias is the whole game.** People post when they are angry or when they are
  recruiting. Say which you are reading; a claim like "sentiment is negative" without a
  base rate means almost nothing.
- **Company size.** Ten complaints at a 200-person company is a pattern. Ten at a
  200,000-person company is Tuesday. Normalise, or say you cannot.
- **Age.** Anything older than 18 months describes a company that may no longer exist in
  that form, particularly after a leadership change or a layoff.
- **The GCC question.** For an Indian subsidiary of a foreign parent, headquarters culture
  and India-site culture are different companies. Cite accounts from the right site, and
  say which site each account describes.
- **GitHub is not evidence for every company.** A bank or a healthcare firm with no public
  repos is not therefore a bad place to work. Set `eng_output_signal` only where public
  output is a reasonable expectation, and record a gap otherwise.
