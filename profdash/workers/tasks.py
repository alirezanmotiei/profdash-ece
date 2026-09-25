"""Agent task worker: fetch_papers + draft_email.

Drains the agent_tasks queue created from the dashboard (or CLI):
  * fetch_papers — DBLP API -> parse -> pick 3-4 most relevant recent
    papers using your venue tiers -> paper_recommendations.
  * draft_email — compose outreach from paper_recommendations + scoring
    why, save as Gmail draft when configured; always stored in the task
    result payload so the dashboard can render it.

Golden rule inherited from the original pipeline: papers and evidence MUST
come from real fetched sources. If a source is unreachable, fail the task
honestly (status='failed', error_message populated) — never invent.

Run via cron/timer:  prof worker tasks --batch-size 5
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from ..gmail import client as gmail_client
from ..profile import Profile, load_profile
from ..scoring.digest import MIRRORS, USER_AGENT, has_hits, name_forms, looks_rate_limited, looks_suspicious_empty
from urllib.parse import quote

SKIP_TYPES = {"Informal and Other Publications", "Editorship", "Reference Works"}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# --- DBLP fetching / parsing -------------------------------------------------


def fetch_dblp_xml(professor_name: str, client: httpx.Client,
                   dblp_url: Optional[str] = None) -> Optional[str]:
    """Fetch publication XML for this author. Tries the professor's stored
    dblp_url first if it points at a pid page, then author-facet queries in
    several case/order forms."""
    urls: list[str] = []
    if dblp_url and "dblp.org/pid/" in dblp_url:
        # pid pages have an XML twin: .../pid/xx/yy.html -> .../pid/xx/yy.xml
        pid_xml = re.sub(r"\.html?$", ".xml", dblp_url)
        if "dblp.org" in pid_xml:
            pid_xml = pid_xml.replace("https://dblp.org", MIRRORS[0])
            urls.append(pid_xml)
    for form in name_forms(professor_name):
        q = "author:" + form.replace(" ", "_") + ":"
        urls.append(
            f"{MIRRORS[0]}/search/publ/api?q={quote(q)}&format=xml&h=30")

    for url in urls:
        try:
            r = client.get(url, headers={"User-Agent": USER_AGENT},
                           follow_redirects=True, timeout=30)
            xml_text = r.text or ""
        except Exception:
            continue
        # Require at least one hit: an empty <result> usually means the name
        # form was wrong, not a paperless author.
        if xml_text and "<result>" in xml_text and has_hits(xml_text):
            return xml_text
        if looks_suspicious_empty(xml_text) or looks_rate_limited(xml_text):
            continue
    return None


def _name_signature(name: str) -> str:
    """Order-insensitive comparison key: sorted words, lowercased."""
    return "".join(sorted(name.lower().replace("-", "").split()))


def get_author_name(xml_text: str, professor_name: str) -> Optional[str]:
    """Canonical author spelling from DBLP <completions>, for filtering."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    target_sig = _name_signature(professor_name)
    for c in root.iter("c"):
        c_text = "".join(c.itertext()).strip()
        name_part = (c_text.rsplit(":", 1)[-1].replace("_", " ").strip()
                     if ":" in c_text else c_text.strip())
        if _name_signature(name_part) == target_sig:
            return name_part
    return None


def parse_dblp_xml(xml_text: str, author_name: Optional[str] = None) -> list[dict]:
    papers = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return papers
    for hit in root.iter("hit"):
        info = hit.find("info")
        if info is None:
            continue
        if author_name:
            authors_el = info.find("authors")
            if authors_el is not None:
                names = ["".join(a.itertext()).strip() for a in authors_el.iter("author")]
                clean_names = [re.sub(r"\s+\d+$", "", n) for n in names]
                if author_name not in clean_names:
                    continue
        title_el, year_el = info.find("title"), info.find("year")
        venue_el, url_el = info.find("venue"), info.find("url")
        ee_el, type_el = info.find("ee"), info.find("type")
        if title_el is None or year_el is None:
            continue
        title = "".join(title_el.itertext()).strip().rstrip(".")
        venue = venue_el.text.strip() if venue_el is not None and venue_el.text else ""
        dblp_url = url_el.text.strip() if url_el is not None and url_el.text else ""
        ee_url = ee_el.text.strip() if ee_el is not None and ee_el.text else ""
        paper_type = type_el.text.strip() if type_el is not None and type_el.text else ""
        if dblp_url.startswith("URL#") or "dblp.org" not in dblp_url:
            dblp_url = ""
        try:
            year = int(year_el.text.strip())
        except ValueError:
            continue
        papers.append({"title": title, "venue": venue, "year": year,
                       "dblp_url": dblp_url, "ee_url": ee_url, "type": paper_type})
    return papers


def classify_venue(venue: str, strong: set[str], moderate: set[str]) -> str:
    v = venue.lower().strip()
    for tv in strong:
        if tv in v or v in tv:
            return "strong"
    for tv in moderate:
        if tv in v or v in tv:
            return "moderate"
    return "unknown"


def select_relevant_papers(papers: list[dict], profile: Profile,
                           limit: int = 4) -> list[dict]:
    """Recent conference papers at your strongest venues first.

    Scored by recency x tier; never padded with old filler.
    """
    current_year = datetime.date.today().year
    cutoff = current_year - 3
    candidates = []
    for p in papers:
        if p["year"] < cutoff:
            continue
        if p.get("type") in SKIP_TYPES:
            continue
        if "CoRR" in p.get("venue", ""):
            continue  # arXiv preprints
        tier = classify_venue(p["venue"], set(profile.venues.strong),
                              set(profile.venues.moderate))
        score = p["year"] * 10
        if tier == "strong":
            score += 100
        elif tier == "moderate":
            score += 50
        candidates.append({**p, "_score": score, "_tier": tier})

    candidates.sort(key=lambda x: x["_score"], reverse=True)

    seen, unique = set(), []
    for c in candidates:
        key = c["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    selected = unique[:limit]
    interests = (profile.identity.interests_short or "").strip() or "the research area"
    for s in selected:
        tier = s.pop("_tier", "unknown")
        s.pop("_score", None)
        if tier == "strong":
            s["relevance_note"] = (f"Core venue ({s['venue']}). "
                                   f"Directly relevant to {interests}.")
        elif tier == "moderate":
            s["relevance_note"] = (f"Adjacent venue ({s['venue']}). "
                                   f"Related to {interests}.")
        else:
            s["relevance_note"] = f"Published at {s['venue']}. Relevant to research profile."
    return selected


# --- task plumbing ---------------------------------------------------------------


def claim_pending_tasks(conn: sqlite3.Connection, batch_size: int) -> list[dict]:
    tasks = conn.execute(
        """SELECT * FROM agent_tasks WHERE status = 'pending'
           ORDER BY requested_at ASC LIMIT ?""", (batch_size,)).fetchall()
    claimed = []
    for task in tasks:
        conn.execute("""UPDATE agent_tasks SET status='in_progress',
                        started_at=? WHERE id=?""", (_now(), task["id"]))
        claimed.append(dict(task))
    if claimed:
        conn.commit()
    return claimed


def get_professor(conn, professor_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM professors WHERE id=?",
                       (professor_id,)).fetchone()
    return dict(row) if row else None


def get_paper_recs(conn, professor_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM paper_recommendations WHERE professor_id=? ORDER BY rank",
        (professor_id,)).fetchall()
    return [dict(r) for r in rows]


def mark_done(conn, task_id: int, summary: str, payload: str = ""):
    conn.execute("""UPDATE agent_tasks SET status='done', completed_at=?,
                    result_summary=?, result_payload=? WHERE id=?""",
                 (_now(), summary, payload, task_id))
    conn.commit()


def mark_failed(conn, task_id: int, error: str):
    conn.execute("""UPDATE agent_tasks SET status='failed', completed_at=?,
                    error_message=? WHERE id=?""", (_now(), error, task_id))
    conn.commit()


def log(msg: str):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --- task implementations ----------------------------------------------------------

BAD_TITLE_PREFIXES = (
    "author response",
    "correction to",
    "publisher correction",
    "author correction",
    "corrigendum",
    "erratum",
    "reply to",
    "response to",
)


def fetch_openalex_papers(openalex_id: Optional[str], professor_name: str,
                          client: httpx.Client, limit: int = 25,
                          mailto: Optional[str] = None) -> list[dict]:
    """Fetch recent papers from OpenAlex. Fallback for non-CS fields (ECE/BME/Circuits)."""
    import os
    papers = []
    if not mailto:
        mailto = os.environ.get("OPENALEX_MAILTO", "")

    def _clean_and_add(results):
        added = []
        for w in results:
            w_type = (w.get("type") or "").lower()
            if w_type in ("erratum", "paratext", "editorial", "letter"):
                continue
            title = (w.get("title") or "").strip().rstrip(".")
            if not title:
                continue
            if any(title.lower().startswith(bad) for bad in BAD_TITLE_PREFIXES):
                continue
            y = w.get("publication_year")
            src = ((w.get("primary_location") or {}).get("source") or {})
            venue = src.get("display_name") or ""
            doi = w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or ""
            added.append({
                "title": title,
                "venue": venue,
                "year": int(y) if y else 0,
                "dblp_url": "",
                "ee_url": doi,
                "type": w_type or "article"
            })
        return added

    # Strategy 1: if openalex_id is present
    if openalex_id:
        url = f"https://api.openalex.org/works?filter=author.id:{openalex_id}&sort=publication_date:desc&per-page={limit}"
        if mailto:
            url += f"&mailto={mailto}"
        try:
            r = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=25, follow_redirects=True)
            if r.status_code == 200:
                papers = _clean_and_add(r.json().get("results", []))
                if papers:
                    return papers
        except Exception as e:
            log(f"  OpenAlex author works fetch error: {e}")

    # Strategy 2: search by author name
    clean_name = re.sub(r"\s+", " ", professor_name.strip())
    url = (f"https://api.openalex.org/works?filter=default.search:{quote(clean_name)},"
           f"publication_year:>2019&sort=publication_date:desc&per-page={limit}")
    if mailto:
        url += f"&mailto={mailto}"
    try:
        r = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=25, follow_redirects=True)
        if r.status_code == 200:
            papers = _clean_and_add(r.json().get("results", []))
    except Exception as e:
        log(f"  OpenAlex name search fallback error: {e}")

    return papers


def process_fetch_papers(conn, task: dict, profile: Profile,
                         client: httpx.Client) -> None:
    professor_id = task["professor_id"]
    prof = get_professor(conn, professor_id)
    if not prof:
        mark_failed(conn, task["id"], f"Professor {professor_id} not found in DB")
        return

    prof_name = prof["professor"]
    log(f"  Fetching publications for {prof_name}...")
    papers = []

    # 1. Try DBLP first
    xml_text = fetch_dblp_xml(prof_name, client, prof.get("dblp_url"))
    if xml_text:
        author_name = get_author_name(xml_text, prof_name)
        papers = parse_dblp_xml(xml_text, author_name=author_name)
        if papers:
            log(f"  Parsed {len(papers)} papers from DBLP")

    # 2. Fall back to OpenAlex (essential for ECE / BME)
    if not papers:
        log(f"  Querying OpenAlex for {prof_name}...")
        user_mailto = os.environ.get("OPENALEX_MAILTO") or profile.identity.email or None
        papers = fetch_openalex_papers(prof.get("openalex_id"), prof_name, client, mailto=user_mailto)
        if papers:
            log(f"  Parsed {len(papers)} papers from OpenAlex")

    if not papers:
        mark_failed(conn, task["id"],
                    f"No publication records found on DBLP or OpenAlex for {prof_name!r}")
        return

    selected = select_relevant_papers(papers, profile, limit=4)
    if not selected:
        log(f"  No tier match; selecting newest 3 papers as general publications")
        papers.sort(key=lambda x: x.get("year", 0), reverse=True)
        selected = papers[:3]
        for s in selected:
            s["relevance_note"] = f"Recent publication at {s.get('venue') or 'journal'} ({s.get('year')})."

    conn.execute("DELETE FROM paper_recommendations WHERE professor_id=?",
                 (professor_id,))
    for i, rec in enumerate(selected, 1):
        conn.execute(
            """INSERT INTO paper_recommendations
               (professor_id, title, venue, year, relevance_note, source_url,
                fetched_at, rank) VALUES (?,?,?,?,?,?,?,?)""",
            (professor_id, rec.get("title", ""), rec.get("venue", ""),
             rec.get("year", 0), rec.get("relevance_note", ""),
             rec.get("dblp_url") or rec.get("ee_url", ""), _now(), i))
    conn.commit()

    titles = [s["title"] for s in selected]
    summary = f"{len(selected)} papers found: {'; '.join(titles[:3])}"
    if len(titles) > 3:
        summary += f" (+{len(titles) - 3} more)"
    mark_done(conn, task["id"], summary)
    log(f"  Saved {len(selected)} paper recommendations")


def compose_email(prof: dict, recs: list[dict], profile: Profile) -> tuple[str, str]:
    """High-converting outreach draft generated dynamically from profile identity + recent papers."""
    ident = profile.identity
    last_name = prof["professor"].split()[-1] if prof["professor"] else "Professor"
    university = prof.get("university", "your university")
    sender_name = ident.name.strip() or "Prospective Student"

    paper_refs = []
    for rec in recs[:2]:
        raw_title = rec.get("title", "").strip().strip('"').strip("'")
        if not raw_title:
            continue
        venue = (rec.get("venue") or "").strip()
        year = rec.get("year")
        if venue and year:
            paper_refs.append(f'"{raw_title}" ({venue}, {year})')
        elif venue:
            paper_refs.append(f'"{raw_title}" ({venue})')
        elif year:
            paper_refs.append(f'"{raw_title}" ({year})')
        else:
            paper_refs.append(f'"{raw_title}"')

    subject = f"Prospective Graduate Student Inquiry — {university} / {sender_name}"

    parts = [f"Dear Professor {last_name},", ""]

    # Paragraph 1: Direct Hook & Interest
    parts.append(
        f"I am writing to express my strong interest in joining your research group at {university} "
        f"as a prospective graduate student (open to PhD or thesis-based Master's programs)."
    )
    parts.append("")

    # Paragraph 2: Specific Paper Engagement
    if paper_refs:
        if len(paper_refs) == 1:
            parts.append(
                f"I recently read your paper {paper_refs[0]} and was particularly interested in your "
                f"methodological approach and its implications for advancing the field."
            )
        else:
            parts.append(
                f"I recently read your papers {paper_refs[0]} and {paper_refs[1]}, and was particularly inspired "
                f"by your methodological approach and its experimental validation."
            )
        parts.append("")

    # Paragraph 3: Candidate Background & Research Alignment (from local profile.toml)
    if ident.bio.strip():
        parts.append(ident.bio.strip())
        parts.append("")
    elif ident.interests_short.strip():
        parts.append(
            f"My research background and interests center on {ident.interests_short.strip()}."
        )
        parts.append("")

    # Paragraph 4: Alignment & Call to Action
    parts.append(
        f"Given your laboratory's current directions, I would be thrilled to contribute to your ongoing projects. "
        f"If you anticipate having openings for new graduate students, I would be deeply grateful "
        f"for the opportunity to have a brief 15-minute conversation to discuss potential fit."
    )
    parts.append("")
    parts.append(
        "I have attached my CV for your review, and I would be delighted to provide any transcripts or project "
        "materials upon request."
    )
    parts.append("")
    parts.append("Sincerely,")
    sig = ident.signature.strip()
    if sig:
        parts.append(sig)
    else:
        parts.append(sender_name)

    return subject, "\n".join(parts)


def process_draft_email(conn, task: dict, profile: Profile,
                        client: httpx.Client) -> None:
    professor_id = task["professor_id"]
    prof = get_professor(conn, professor_id)
    if not prof:
        mark_failed(conn, task["id"], f"Professor {professor_id} not found in DB")
        return

    recs = get_paper_recs(conn, professor_id)
    if not recs:
        log("  No paper recommendations yet - auto-fetching papers first...")
        fetch_task_id = conn.execute(
            """INSERT INTO agent_tasks (professor_id, task_type, status, requested_at)
               VALUES (?, 'fetch_papers', 'in_progress', ?)""",
            (professor_id, _now())).lastrowid
        conn.commit()
        fetch_task = dict(conn.execute(
            "SELECT * FROM agent_tasks WHERE id=?", (fetch_task_id,)).fetchone())
        process_fetch_papers(conn, fetch_task, profile, client)
        recs = get_paper_recs(conn, professor_id)

    subject, body = compose_email(prof, recs, profile)

    gmail_saved, gmail_error = False, ""
    to_email = prof.get("email", "")
    if to_email:
        try:
            service = gmail_client.get_service(profile.gmail.token_path or None)
            gmail_client.create_draft(service, to_email, subject, body)
            gmail_saved = True
        except Exception as e:
            gmail_error = str(e)[:200]
    else:
        gmail_error = "no email address on record"

    if gmail_saved:
        summary = f"Draft saved to Gmail: {subject}"
        log(f"  {summary}")
    else:
        summary = (f"Draft saved to task result "
                   f"(Gmail unavailable: {gmail_error or 'not configured'})")
        log(f"  {summary}")

    payload = json.dumps({
        "professor_id": professor_id, "subject": subject, "body": body,
        "gmail_saved": gmail_saved, "gmail_error": gmail_error,
    }, ensure_ascii=False)
    mark_done(conn, task["id"], summary, payload)


# --- entry point ----------------------------------------------------------------------


def process_batch(db_path, batch_size: int = 5, profile: Profile | None = None) -> int:
    profile = profile or load_profile()
    conn = connect(db_path)
    try:
        tasks = claim_pending_tasks(conn, batch_size)
        if not tasks:
            log("No pending tasks.")
            return 0
        log(f"Claimed {len(tasks)} task(s)")
        processed = 0
        with httpx.Client(timeout=30) as client:
            for task in tasks:
                task_id, task_type = task["id"], task["task_type"]
                log(f"  Task #{task_id}: {task_type} for {task['professor_id']}")
                try:
                    if task_type == "fetch_papers":
                        process_fetch_papers(conn, task, profile, client)
                    elif task_type == "draft_email":
                        process_draft_email(conn, task, profile, client)
                    else:
                        mark_failed(conn, task_id, f"Unknown task_type: {task_type}")
                    processed += 1
                except Exception as e:
                    log(f"  Error: {e}")
                    mark_failed(conn, task_id, str(e))
        return processed
    finally:
        conn.close()


def queue_status(db_path) -> list[tuple[str, int]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM agent_tasks GROUP BY status").fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        conn.close()
