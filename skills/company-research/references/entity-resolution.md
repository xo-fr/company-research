# Entity resolution

The brand on the job posting is rarely the entity that will employ you, and almost never
the entity that files the documents worth reading. Get this wrong and every pillar below
it is researching a different company.

```bash
python $CR/scripts/resolve.py --jd-url "<posting>" --market IN --pretty \
  --jd-out <dossier>/jd.txt
python $CR/scripts/resolve.py --name "Zomato" --market IN --pretty
```

## Reading the output

| Field | What to do with it |
|---|---|
| `confidence` | `< 0.7` → **ask the user**, showing `candidates` with their descriptions |
| `candidates` | Present at all → the script declined to choose. You must not choose either |
| `tree.entities` | Bind each pillar to the entity in the market being hired for |
| `employment_type` | `unknown` in IN → ask. `vendor` → tell the user before researching |
| `notes` | Says exactly which identifier could not be found and why |

## The three shapes to recognise

**One brand, two filers.** Infosys resolves to `us:0001067491` (a 20-F filer with the
SEC) and `in:L85110KA1981PLC013115` (BSE 500209). The 20-F is far richer than anything
on the Indian side — read it even when the role is in Bengaluru — but headcount, attrition
and pay policy in the India entity are what the candidate actually joins.

**Brand renamed, entity unchanged.** Zomato → Eternal Ltd, Facebook → Meta, Square →
Block. Wikidata usually knows the alias; the ticker and CIN do not change. If the script
returns several near-identical candidates, this is usually why — ask.

**GCC or vendor.** An Indian posting for a US brand is often an Indian subsidiary (a
global capability centre) or, worse, a staffing vendor placing you at the brand. These
differ in pay band, job security, and what "working at X" means on a resume. When
`employment_type` is `gcc` or `vendor`, say so in the dossier's summary — candidates
frequently do not know.

## When the script comes up empty

- **Unlisted Indian company**: no CIN and no BSE code is the normal case, not a failure.
  Bind pillars to the brand, note it, and expect `financial_health` to be `partial`.
- **US private company**: often has a CIK anyway from Reg D filings — worth checking
  `edgar.py --forms D`, which dates the last raise and feeds `funding_months_ago`.
- **ATS-hosted posting** (`ats_host: true`): the domain came from the posting body, not
  the URL. Sanity-check it against the company's own careers page before trusting it.

## Never

- Never merge two entities because the names look similar. `Acme Inc` and
  `Acme Technologies Pvt Ltd` may be unrelated companies in different countries.
- Never continue past a low-confidence resolution to "save the user a question". One
  question costs a turn; a wrong entity costs the whole dossier.
