# profdash-ece — OpenAlex discovery for ECE/BME faculty

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Upstream: sahroush/profdash](https://img.shields.io/badge/Upstream-sahroush%2Fprofdash-blue.svg)](https://github.com/sahroush/profdash)

`profdash-ece` is an **extension** of [profdash](https://github.com/sahroush/profdash) — a self-hosted, AI-assisted professor outreach tracker (MIT, by [@sahroush](https://github.com/sahroush)). The upstream core covers import, AI-assisted scoring, outreach, Gmail reply tracking, and the local dashboard; this repository adds the **OpenAlex-backed discovery layer for Electrical & Biomedical Engineering (ECE/BME)** so that pure ECE/BME professors (analog, RF, telecom, power, signal processing, bioelectronics, biomedical engineering, medical physics) can be imported and scored — CSRankings only covers CS.

> **Credit & Attribution:** Everything *not* listed in "What this extension adds" below is upstream work by [@sahroush](https://github.com/sahroush) — see the [upstream repository](https://github.com/sahroush/profdash) and [upstream README](https://github.com/sahroush/profdash#readme). Both the upstream core and this extension are released under the [MIT License](LICENSE).

## What this extension adds

| Component | Description |
| :-- | :-- |
| `profdash/ingest/openalex.py` | OpenAlex-backed importer: authors from topic subfields 2204 (Biomedical Engineering) and 2208 (Electrical & Electronic Engineering) plus curated adjacent topics (electroporation, radiation therapy, ultrasound, EMG, …), filtered by target countries and seniority thresholds (h-index / works count). Each professor is classified into a `research_bucket`: `biomed`, `circuits`, `ml`, or `ee_general`. |
| `profdash/workers/tasks.py` | **Dual-Engine Paper Discovery & Context-Aware Outreach**: Automated OpenAlex API fallback when DBLP returns 0 hits (critical for ECE/BME faculty), noise-filtered (pruning errata, responses, and editorial paratext), and paired with a cold email generator dynamically injecting candidate bio and recent paper citations. |
| `profdash/dashboard/` | **Interactive Contact Management & Instant Execution**: Added inline HTMX contact editor (email, lab website, Google Scholar), 1-click external academic search links, inline instant worker task execution (`HX-Refresh`), and one-click `mailto:` draft launcher prefilling recipient, subject, and body. |
| `profdash/presets/ee-bio.toml` | Venue tiers for bioelectronics/ECE (Nature Electronics, IEEE JSSC/TBME/TMTT, …). |
| `prompts/scoring-pass-ee.md` | Scoring prompt pack tuned for the ECE/BME pipeline. |
| `tests/` | Automated unit test suite (`pytest`) covering task email generation across research buckets, paper filtering, database contact persistence, and API endpoints. |
| Target countries | AU / JP / KR / SG / TR added to the OpenAlex import targets. |

## Install & Run (ECE edition)

```bash
git clone https://github.com/alirezanmotiei/profdash-ece.git
cd profdash-ece
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

pip install -e .                     # core; see pyproject.toml
pip install -e ".[gmail]"            # optional: Gmail reply tracking

# Import ECE/BME faculty from OpenAlex
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
- Everything beyond the files listed in "What this extension adds" is upstream work; see [LICENSE](LICENSE) (MIT — upstream core and this extension are both released under it).