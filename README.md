# profdash-ece — OpenAlex discovery for ECE/BME faculty

`profdash-ece` is an **extension** of [profdash](https://github.com/sahroush/profdash) — a self-hosted, AI-assisted professor outreach tracker (MIT, by [@sahroush](https://github.com/sahroush)). The upstream core covers import, AI-assisted scoring, outreach, Gmail reply tracking, and the local dashboard; this repository adds the **OpenAlex-backed discovery layer for Electrical & Biomedical Engineering (ECE/BME)** so that pure ECE/BME professors (analog, RF, telecom, power, signal processing, bioelectronics, biomedical engineering, medical physics) can be imported and scored — CSRankings only covers CS.

> **Credit:** everything *not* listed in "What this fork adds" below is upstream work by [@sahroush](https://github.com/sahroush) — see the [upstream README](https://github.com/sahroush/profdash#readme).

## What this fork adds

| Component | Description |
| :-- | :-- |
| `profdash/ingest/openalex.py` | OpenAlex-backed importer: authors from topic subfields 2204 (Biomedical Engineering) and 2208 (Electrical & Electronic Engineering) plus curated adjacent topics (electroporation, radiation therapy, ultrasound, EMG, …), filtered by target countries and seniority thresholds (h-index / works count). Each professor is classified into a `research_bucket`: `biomed`, `circuits`, `ml`, or `ee_general` (new columns: `openalex_id`, `research_bucket`). |
| `prof digest` fallback | When an author is missing from DBLP (most EE professors are), publications fall back to OpenAlex, and journals are tiered with the `ee-bio` venue preset. |
| `profdash/presets/ee-bio.toml` | Venue tiers for bioelectronics/ECE (Nature Electronics, IEEE JSSC/TBME/TMTT, …). |
| `prompts/scoring-pass-ee.md` | Scoring prompt pack tuned for the ECE/BME pipeline. |
| Target countries | AU / JP / KR / SG / TR added to the OpenAlex import targets. |

## Install & Run (ECE edition)

```bash
git clone https://github.com/alirezanmotiei/profdash-ece.git
cd profdash-ece
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .                     # core; see pyproject.toml
pip install -e ".[gmail]"            # optional: Gmail reply tracking

# import ECE/BME faculty from OpenAlex
prof import openalex --country US --country CA --country DE --country CH \
                     --country GB --country AT --country SE --country NO \
                     --country DK --country NL --country FR --country IT \
                     --country ES --country BE
prof serve
```

Set `OPENALEX_MAILTO=you@example.com` to join the OpenAlex polite pool.

**Honest limitations:** OpenAlex has no "professor" flag — seniority is proxied by h-index/works-count, and the AI scoring pass (with its evidence rules) is the human-in-the-loop filter for industry researchers and merged-author junk. Emails/homepages are not included; the scoring agent finds them live.

## Upstream

- Core project: **[sahroush/profdash](https://github.com/sahroush/profdash)** — self-hosted professor outreach tracker (CSRankings import, AI-assisted scoring, Gmail reply scanning, local dashboard), MIT.
- Full feature documentation: [upstream README](https://github.com/sahroush/profdash#readme).
- Everything beyond the files listed in "What this fork adds" is upstream work; see [LICENSE](LICENSE) (MIT — upstream core and this extension are both released under it).