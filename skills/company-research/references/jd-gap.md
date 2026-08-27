# Pillar: jd_gap

The job description against this specific candidate: what matches, what is missing, and
what the JD reveals about the team that the team did not mean to reveal.

The JD is the highest-signal input the tool receives. It is written by the hiring manager,
about the actual work, in the last few weeks.

## Inputs

- `query.jd_text_path` — the extracted posting text, written by `resolve.py --jd-out`.
- `profile.resume_path` — read it if present. If it is a PDF you cannot parse, say so and
  work from what the user has told you rather than guessing.

## What to produce

Three lists, in this order:

1. **Strong matches** — requirement, and the evidence in the resume that meets it.
2. **Gaps** — requirement with no evidence, split into *learnable before the interview*
   and *structural*. Be blunt; a candidate who walks in unprepared for the obvious
   question is worse off than one who was told.
3. **What the JD gives away** — read the posting as a document about the team.

## Reading a JD for what it gives away

- **"Wear many hats", "thrive in ambiguity", "self-starter"** → little process, and
  probably an understaffed team.
- **A five-year stack list for a mid-level role** → they are backfilling someone specific
  who did all of it, and have not decided what to drop.
- **"Fast-paced" plus "on-call" plus no mention of team size** → ask about team size and
  rotation depth first.
- **Requirements copied verbatim from an older posting** (check the Wayback series for the
  same URL) → the role has been open a long time, or nobody re-scoped it.
- **A named internal system or an unusual library** → excellent interview preparation
  material, and a good question to ask.
- **Compliance or certification language** (SOC 2, RBI, HIPAA) → real constraints on how
  the engineering work feels day to day.

## Signals owned by this pillar

- `comp_transparency` (bool) — does the posting itself publish a pay range? Read it from
  the JD text, and only from there.
- `stack_currency` (0..1) — only when the `culture` pillar is not running this stage;
  never write the same signal from two fragments.

## What a good claim looks like

- *"The JD asks for Kafka, Flink, Iceberg and dbt for one mid-level role, and names an
  internal system ('Nimbus') twice — the shape of a backfill for a departed specialist."*
- *"Resume shows three years of Kafka in production but no stream-processing framework;
  Flink is the one gap that will come up, and it is learnable in a week to interview
  depth."*
- Not: *"Good fit overall."*

## Traps

- **Do not flatter.** The user is deciding where to spend a week of preparation. A gap
  list that reads as reassurance wastes that week.
- **Do not invent resume content.** If the resume is unreadable, mark the pillar
  `partial`, list what the JD requires, and ask the user to confirm coverage.
- **JD requirement lists are aspirational.** Say which requirements look like hard filters
  (years, a specific certification, on-site presence) versus wish-list items.
