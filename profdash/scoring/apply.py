"""Apply a completed scoring judgment for one professor.

The judgment contract is deliberately simple JSON so ANY tool can produce
it (a human, Claude Code, Codex, a script):

    {
      "professor_id": "aaron-roth-0001-university-of-pennsylvania",
      "fit_tier": "Strong",
      "phd_recommendation": "MUST APPLY",
      "final_score_phd": 9,
      "phd_why": "Active in mechanism design; recent STOC/FOCS stream ...",
      "phd_funding_confidence": "likely",
      "masters_recommendation": null,
      "final_score_masters": null,
      "masters_why": null,
      "masters_funding_confidence": null,
      "evidence": [
        {"field_name": "research_fit",
         "claim": "Published 3 papers at top theory venues since 2024",
         "source_type": "dblp",
         "source_url": "https://dblp.org/pid/...",
         "quote": "2026 [STOC] ..."}
      ]
    }

Rules carried over from the original pipeline:
  * only rows in stage='triaged' may be scored (refuses re-review)
  * rec/score/why must be consistently NULL or consistently set
  * >= 2 evidence rows quoting REAL fetched text — never invent sources
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

FIT_TIERS = {"Strong", "Moderate", "Lower", "Drop"}
RECOMMENDATIONS = {"MUST APPLY", "GOOD FIT", "MEDIUM FIT",
                   "BACKUP / LOW FIT", "DO NOT APPLY"}
FUNDING = {"likely", "confirmed", "uncertain", "unknown"}


class JudgmentError(ValueError):
    pass


def validate(judgment: dict) -> None:
    pid = judgment.get("professor_id")
    if not pid:
        raise JudgmentError("missing professor_id")

    fit = judgment.get("fit_tier")
    if fit not in FIT_TIERS:
        raise JudgmentError(f"fit_tier must be one of {sorted(FIT_TIERS)}, got {fit!r}")

    for track in ("phd", "masters"):
        rec = judgment.get(f"{track}_recommendation")
        score = judgment.get(f"final_score_{track}")
        why = judgment.get(f"{track}_why")
        fund = judgment.get(f"{track}_funding_confidence")
        if rec is not None and rec not in RECOMMENDATIONS:
            raise JudgmentError(f"{track}_recommendation invalid: {rec!r}")
        if fund is not None and fund not in FUNDING:
            raise JudgmentError(f"{track}_funding_confidence invalid: {fund!r}")
        if rec is None:
            if score is not None or why is not None:
                raise JudgmentError(
                    f"{track}: recommendation NULL but score/why set — use all-NULL or all-set")
        else:
            if not isinstance(score, int) or not 0 <= score <= 10:
                raise JudgmentError(f"{track}: final_score must be int 0-10")
            if not why or len(str(why)) <= 20:
                raise JudgmentError(f"{track}: why must explain the call (>20 chars)")

    ev = judgment.get("evidence") or []
    if len(ev) < 2:
        raise JudgmentError("need >=2 evidence rows quoting real fetched text")
    for e in ev:
        for k in ("claim", "source_url", "quote"):
            if not e.get(k):
                raise JudgmentError(f"evidence row missing {k!r}")


def apply_judgment(db_path: Path, judgment: dict) -> str:
    """Validate + write. Commits per professor. Returns the professor id."""
    validate(judgment)
    pid = judgment["professor_id"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    conn = sqlite3.connect(str(db_path), timeout=15)
    try:
        with conn:
            cur = conn.execute(
                """UPDATE professors SET stage='deep_reviewed',
                       reviewed_at=?, fit_tier=?,
                       phd_recommendation=?, final_score_phd=?, phd_why=?,
                       phd_funding_confidence=?,
                       masters_recommendation=?, final_score_masters=?, masters_why=?,
                       masters_funding_confidence=?
                   WHERE id=? AND stage='triaged'""",
                (now, judgment["fit_tier"],
                 judgment.get("phd_recommendation"),
                 judgment.get("final_score_phd"),
                 judgment.get("phd_why"),
                 judgment.get("phd_funding_confidence"),
                 judgment.get("masters_recommendation"),
                 judgment.get("final_score_masters"),
                 judgment.get("masters_why"),
                 judgment.get("masters_funding_confidence"),
                 pid))
            if cur.rowcount != 1:
                row = conn.execute("SELECT stage FROM professors WHERE id=?",
                                   (pid,)).fetchone()
                state = row[0] if row else "not found"
                raise JudgmentError(
                    f"professor {pid} not updated (stage={state!r}; only 'triaged' rows are scorable)")
            for e in judgment["evidence"]:
                conn.execute(
                    """INSERT INTO evidence(professor_id, stage, field_name,
                       claim, source_type, source_url, quote_or_paraphrase, fetched_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (pid, "deep_review", e.get("field_name", "research_fit"),
                     e["claim"], e.get("source_type", ""),
                     e["source_url"], e["quote"], now))
    finally:
        conn.close()
    return pid


def apply_file(db_path: Path, path: str | Path) -> str:
    """Apply one judgment from a JSON file."""
    with open(path) as f:
        return apply_judgment(db_path, json.load(f))
