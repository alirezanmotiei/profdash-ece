# Scoring pass prompt pack — ECE/BME edition (OpenAlex-backed)

Paste this into Claude Code, Codex, or any coding agent, from the
directory that contains `data/profdash.sqlite`. Fill in the two
<ANGLE BRACKET> placeholders first.

Differences from the CS pack: digests are DBLP-first with an automatic
OpenAlex fallback (digest header shows `source=openalex`), so venue
tiering uses journal names from `[venues]` in profile.toml.

---

You are running the scoring pass of my professor-outreach pipeline
(profdash). Work autonomously, batch by batch, and report progress.

## Context

- I am a prospective PhD applicant in electrical/biomedical engineering.
  My research interests: <PASTE FROM profile.toml [identity]>
- Strong/moderate venues in my field: <PASTE FROM profile.toml [venues]>
- Database: data/profdash.sqlite (SQLite). Professors with
  stage='triaged' are the backlog to score. Scored rows are
  stage='deep_reviewed'. The `research_bucket` column hints the area:
  biomed (bioelectronics/BME), circuits (analog/RF/signal processing),
  ml (machine learning), ee_general (power/control/other ECE).

## Per-professor workflow

1. Fetch the publication digest: `prof digest <professor-id>`
   (DBLP-first, OpenAlex fallback, cached 24h under data/sources_cache/).
2. If the digest is an error JSON (`dblp_no_hits`), try the professor's
   personal page / Google Scholar / OpenAlex author page in the browser
   or via the API (their OpenAlex id is in the `openalex_id` column).
   If you still cannot find ANY real publication evidence, leave the
   professor unscored and note it.
3. Judge research fit for a prospective PhD applicant with my interests.
   Weigh: recent (last ~3 years) output at strong venues, activity
   level, trajectory, and anything the digest shows about group
   vitality. Verify the person is actually a professor (OpenAlex
   sometimes includes industry researchers — check the university page).
4. Write a judgment JSON to /tmp/judgments/<professor-id>.json using
   the schema below. Include >= 2 evidence rows, each quoting the
   digest or another fetched source VERBATIM with its URL.
5. Apply it: `prof apply /tmp/judgments/<professor-id>.json`
   (validation errors mean: fix the JSON, not bypass the rules).

## Judgment schema

{
  "professor_id": "<id>",
  "fit_tier": "Strong|Moderate|Lower|Drop",
  "phd_recommendation": "MUST APPLY|GOOD FIT|MEDIUM FIT|BACKUP / LOW FIT|DO NOT APPLY",
  "final_score_phd": <int 0-10>,
  "phd_why": "<>20 chars, cite concrete digest facts",
  "phd_funding_confidence": "likely|confirmed|uncertain|unknown",
  "masters_recommendation": null,
  "final_score_masters": null,
  "masters_why": null,
  "masters_funding_confidence": null,
  "evidence": [
    {"field_name": "research_fit", "claim": "...",
     "source_type": "openalex|dblp|web|email",
     "source_url": "https://openalex.org/... or https://dblp.org/...",
     "quote": "<verbatim>"}
  ]
}

## Hard rules

- NEVER invent papers, venues, quotes, or funding facts. Every evidence
  quote must come from text you actually fetched this session.
- Unreachable/empty source => leave the professor unscored (stay
  'triaged') and note it in the batch report. Fail honestly.
- DO NOT APPLY only on hard facts (emeritus, left academia, public
  "not taking students"). Never on geography, prestige, or name.
- Masters fields: always NULL (this applicant is PhD-only).

## Calibration anchors

- 9-10 MUST APPLY: recent output at strong venues (Nature-family,
  IEEE JSSC/TBME/TMI/TMTT-level) + active group + funding likely.
- 7-8 GOOD FIT: active and relevant; some uncertainty.
- 5-6 MEDIUM FIT: relevant field, slower/adjacent output.
- 3-4 BACKUP / LOW FIT: weak recent output in my area.
- <=2 / DO NOT APPLY: hard facts only.
- Funding: 'confirmed' beats 'likely'; scholarship-dependent systems
  are 'uncertain'.

## Batch protocol

- Take professors in id order, batches of ~10.
- After each batch print one line:
  `scored X/N | MUST APPLY a, GOOD FIT b, MEDIUM FIT c, BACKUP d, DNP e | failed f`
- At the end run `prof audit` and summarize remaining backlog.
