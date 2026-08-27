# Pillar: interviewers

The user pastes the names on the calendar invite; you return what those people have
published. Ten minutes of this is the highest-leverage preparation available anywhere.

## Inputs

Names, and ideally the company. If the user has not supplied names, this pillar is
`status: "gap"` with the reason "no interviewer names provided" — do not go hunting for
who *might* interview them.

## Sources

1. **GitHub**: `https://api.github.com/search/users?q=<name>+<company>` then the user's
   repos and recent public activity.
2. **Conference talks and papers**: web search for the name plus the company; arXiv and
   ACM/IEEE listings where relevant.
3. **The company's own engineering blog** — posts are usually bylined.
4. **Personal sites, newsletters, podcasts** they appear on.
5. **Their public writing on Hacker News or Mastodon**, where clearly attributable.

**LinkedIn is out of scope, permanently.** No scraping, no cookie reuse, no
headless-browser session. If a fact is only on LinkedIn, it does not go in the dossier.

## What to produce

Per interviewer: what they work on, what they have built or written recently, and one
specific, non-sycophantic question the user could ask them. The question is the point.

## Signals owned by this pillar

None.

## What a good claim looks like

- *"Wrote the company's 2026 post on migrating from Kafka to Iceberg (byline, 2026-03-14)
  and maintains the open-source connector referenced in it."*
- *"Gave a talk at SRECon 2025 on incident review practice; the deck is public."*
- Suggested question: *"Your post said the Iceberg migration was driven by cost — did the
  operational load land where you expected?"*

## Traps

- **Name collisions are the default risk.** Confirm with a second corroborating signal —
  employer named in the profile, a matching byline — before attributing anything. Getting
  this wrong is not a small error: the user may quote it in the room.
- **Common names, especially transliterated ones.** If you cannot disambiguate, say so and
  return nothing for that person. Ambiguity here is worse than absence.
- **Stale roles.** A 2019 talk describes a job they may have left. Date every item.
- **Only public, professional material.** Nothing personal, nothing from private accounts,
  nothing pieced together across sources into a profile the person did not publish
  themselves. If it feels like surveillance, it is out of scope.
