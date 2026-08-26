"""Read-only database quality report."""
from __future__ import annotations

import sqlite3


def audit(db_path) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    lines = []

    def q1(sql):
        return conn.execute(sql).fetchone()[0]

    total = q1("SELECT COUNT(*) FROM professors")
    lines.append(f"professors: {total}")
    if not total:
        conn.close()
        return ["(empty database — run `prof import csrankings` or `prof demo`)"]

    lines.append("")
    lines.append("by stage:")
    for stage, n in conn.execute(
            "SELECT COALESCE(stage,'(null)'), COUNT(*) FROM professors GROUP BY 1 ORDER BY 2 DESC"):
        lines.append(f"  {stage:<16} {n}")

    lines.append("")
    lines.append("by outreach status:")
    for status, n in conn.execute(
            "SELECT COALESCE(status,'(null)'), COUNT(*) FROM professors GROUP BY 1 ORDER BY 2 DESC"):
        lines.append(f"  {status:<22} {n}")

    unscored = q1("SELECT COUNT(*) FROM professors WHERE stage='triaged'")
    scored = q1("SELECT COUNT(*) FROM professors WHERE stage='deep_reviewed'")
    lines.append(f"")
    lines.append(f"scoring backlog (stage=triaged): {unscored}")
    lines.append(f"deep reviewed:                    {scored}")

    no_evidence = conn.execute("""
        SELECT COUNT(*) FROM professors p
        WHERE p.stage='deep_reviewed'
          AND (SELECT COUNT(*) FROM evidence e WHERE e.professor_id=p.id) < 2""").fetchone()[0]
    if no_evidence:
        lines.append(f"  !! deep_reviewed rows with <2 evidence: {no_evidence}")

    missing_contact = q1(
        "SELECT COUNT(*) FROM professors WHERE status IN ('contacted','no_response') "
        "AND (last_contacted_at IS NULL OR last_contacted_at='')")
    if missing_contact:
        lines.append(f"contacted-but-no-timestamp rows: {missing_contact}")

    dupes = conn.execute("""
        SELECT lower(professor), COUNT(*) c FROM professors
        GROUP BY lower(professor) HAVING c > 1 LIMIT 5""").fetchall()
    if dupes:
        lines.append("")
        lines.append("possible duplicate persons:")
        for name, c in dupes:
            lines.append(f"  {name}: {c} rows")

    try:
        config_rows = conn.execute("SELECT key, value FROM config").fetchall()
        if config_rows:
            lines.append("")
            lines.append("config keys:")
            for k, v in config_rows:
                shown = v if len(str(v)) <= 60 else str(v)[:57] + "..."
                lines.append(f"  {k} = {shown}")
    except sqlite3.OperationalError:
        pass

    conn.close()
    return lines
