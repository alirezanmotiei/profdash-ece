# Scoring the backlog (with an AI agent)

The scoring pass turns a `triaged` professor into `deep_reviewed`: a fit
tier, per-track recommendation + score + rationale, and **evidence rows
that quote real fetched sources**. This is the part of profdash where an
AI coding agent does the heavy lifting — under strict guardrails.

## The loop

```bash
prof digest <professor-id>            # 1. fetch DBLP digest
# 2. judge: read the digest against YOUR profile/interests
cat > judgment.json <<'EOF'           # 3. write the judgment
{ ... }
EOF
prof apply judgment.json              # 4. validated, atomic write
```

Batch helpers:

```bash
prof digest id1 id2 id3 --out /tmp/digests    # digests to files
sqlite3 data/profdash.sqlite "SELECT id FROM professors WHERE stage='triaged'"
# (or use python -c if you don't have the sqlite3 CLI)
```

## Judgment contract

```json
{
  "professor_id": "jane-doe-some-university",
  "fit_tier": "Strong",
  "phd_recommendation": "MUST APPLY",
  "final_score_phd": 9,
  "phd_why": "One sentence: why this score, citing the digest.",
  "phd_funding_confidence": "likely",
  "masters_recommendation": null,
  "final_score_masters": null,
  "masters_why": null,
  "masters_funding_confidence": null,
  "evidence": [
    {"field_name": "research_fit",
     "claim": "Active at strong venues in the last 3 years",
     "source_type": "dblp",
     "source_url": "https://dblp.org/pid/...",
     "quote": "2026 [STOC] ... (verbatim line from the digest)"},
    {"field_name": "funding",
     "claim": "Group page advertises openings",
     "source_type": "web",
     "source_url": "https://...",
     "quote": "\"We have funded openings for ...\""}
  ]
}
```

Validation rules (`prof apply` enforces them):

- `fit_tier` ∈ Strong | Moderate | Lower | Drop
- recommendation ∈ MUST APPLY | GOOD FIT | MEDIUM FIT | BACKUP / LOW FIT | DO NOT APPLY
- score ∈ 0–10; rec/score/why are all-set or all-NULL per track
- **≥ 2 evidence rows, each with a verbatim quote + source URL**
- only rows in `stage='triaged'` can be scored (no silent re-review)

## Prompt pack for your agent

Paste this into Claude Code / Codex / your agent of choice, from the
directory containing `data/profdash.sqlite`:

```text
You are running the scoring pass of my professor-outreach pipeline.

Context:
- My research interests: <paste from profile.toml [identity]>
- My field's strong venues: <paste from profile.toml [venues]>
- Database: data/profdash.sqlite (SQLite; professors.stage='triaged' = backlog)

Workflow per professor (process in batches of ~10):
1. Run: prof digest <id>   (DBLP publication digest; cached 24h)
2. Judge research fit for a prospective PhD applicant with my interests.
   Consider: recent strong-venue output, activity level, trajectory.
3. Write a judgment JSON to /tmp/judgments/<id>.json following the schema
   in docs/scoring.md. Include ≥2 evidence rows quoting the digest
   VERBATIM. Never invent papers, venues, or quotes.
4. Run: prof apply /tmp/judgments/<id>.json

Rules:
- If the digest fails or is empty, leave the professor unscored and note it.
- DO NOT APPLY only on hard facts (e.g. emeritus, left academia), never on
  geography or prestige alone.
- Scores 8-10: recent strong-venue first-author output + active group.
  5-7: solid but slower or funding-unclear. 3-4: backup. Calibrate as you go.
- After each batch, report: N scored, tier counts, failures.

Start with: <paste professor ids or "the 20 lowest-id triaged rows">
```

## Calibration guidance

Score anchors that worked well in a real season:

| Score | Meaning |
|---|---|
| 9–10 | MUST APPLY — recent first-author work at strong venues, active group, likely funding |
| 7–8 | GOOD FIT — active and relevant; some uncertainty (funding, group size) |
| 5–6 | MEDIUM FIT — relevant field but slower output, or strong output in adjacent area |
| 3–4 | BACKUP / LOW FIT — weak recent output in your area |
| ≤2 / DO NOT APPLY | hard facts only: emeritus, departed, explicit "not taking students" |

Funding confidence vocabulary: `confirmed` (posted guarantee) > `likely`
(stipends standard) > `uncertain` (scholarship-dependent systems) >
`unknown`.

## Auditing your pass

```bash
prof audit
```

Flags deep_reviewed rows with <2 evidence rows (the most common shortcut
failure), remaining backlog by country, and duplicate persons.
