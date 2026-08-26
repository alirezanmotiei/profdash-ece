# Scoring pass prompt pack

Paste this into Claude Code, Codex, or any coding agent, from the
directory that contains `data/profdash.sqlite`. Fill in the two
<ANGLE BRACKET> placeholders first.

---

You are running the scoring pass of my professor-outreach pipeline
(profdash). Work autonomously, batch by batch, and report progress.

## Context

- My research interests: <PASTE FROM profile.toml [identity]>
- Strong venues in my field: <PASTE FROM profile.toml [venues]>
- Database: data/profdash.sqlite (SQLite). Professors with
  stage='triaged' are the backlog to score. Scored rows are
  stage='deep_reviewed'.

## Per-professor workflow

1. Fetch the publication digest: `prof digest <professor-id>`
   (DBLP-backed, cached 24h under data/sources_cache/).
2. Judge research fit for a prospective PhD applicant with my interests.
   Weigh: recent (last ~3 years) output at strong venues, activity level,
   trajectory, and anything the digest shows about group vitality.
3. Write a judgment JSON to /tmp/judgments/<professor-id>.json using the
   schema below. Include >= 2 evidence rows, each quoting the digest or
   another fetched source VERBATIM with its URL.
4. Apply it: `prof apply /tmp/judgments/<professor-id>.json`
   (validation errors mean: fix the JSON, not bypass the rules).

## Judgment schema

{
  "professor_id": "<id>",
  "fit_tier": "Strong|Moderate|Lower|Drop",
  "phd_recommendation": "MUST APPLY|GOOD FIT|MEDIUM FIT|BACKUP / LOW FIT|DO NOT APPLY",
  "final_score_phd": <int 0-10>,
  "phd_why": "<>20 chars, cite concrete digest facts",
  "phd_funding_confidence": "likely|confirmed|uncertain|unknown",
  "masters_recommendation": null,      // or same shape as phd
  "final_score_masters": null,
  "masters_why": null,
  "masters_funding_confidence": null,
  "evidence": [
    {"field_name": "research_fit", "claim": "...", "source_type": "dblp",
     "source_url": "https://dblp.org/...", "quote": "<verbatim>"}
  ]
}

## Hard rules

- NEVER invent papers, venues, quotes, or funding facts. Every evidence
  quote must come from text you actually fetched this session.
- Unreachable/empty source => leave the professor unscored (stay
  'triaged') and note it in the batch report. Fail honestly.
- DO NOT APPLY only on hard facts (emeritus, left academia, public
  "not taking students"). Never on geography, prestige, or name.
- Masters fields: NULL when the university has no masters program,
  otherwise score both tracks.

## Calibration anchors

- 9-10 MUST APPLY: recent first-author output at strong venues + active
  group + funding likely.
- 7-8 GOOD FIT: active and relevant; some uncertainty.
- 5-6 MEDIUM FIT: relevant field, slower/adjacent output.
- 3-4 BACKUP / LOW FIT: weak recent output in my area.
- <=2 / DO NOT APPLY: hard facts only.
- Funding: 'confirmed' beats 'likely'; scholarship-dependent systems
  (e.g. CSC in China) are 'uncertain'.

## Batch protocol

- Take professors in id order, batches of ~10.
- After each batch print one line:
  `scored X/N | MUST APPLY a, GOOD FIT b, MEDIUM FIT c, BACKUP d, DNP e | failed f`
- At the end run `prof audit` and summarize remaining backlog.
