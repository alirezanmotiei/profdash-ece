#!/usr/bin/env python3
"""
Gmail reply scanner.

STEP 1 — BACKFILL: Extract professor email addresses from recent sent Gmail
(To: headers). Matches by last name + institution domain, updates professors.email
and professors.last_contacted_at. Runs before scanning so newly emailed profs
are picked up in the same pass.

STEP 2 — SCAN: For each professor with status in ('contacted', 'no_response') and
a non-null email, checks for new messages from that address since their
last_contacted_at (or the last scan, whichever is more recent). For each reply found:

1. Classifies into one of: replied_template / replied_interested / replied_no_funding /
   replied_rejected / conditional_accept / needs_follow_up — WITH a confidence level
   (high/medium/low). Low confidence is a valid, expected output for ambiguous replies.

2. Inserts a status_history row with confirmed=0, the suggested status, and a note
   containing a short quote/paraphrase plus the confidence level.

3. Does NOT update professors.status directly — that only happens on human confirmation
   via the dashboard's Confirmations page.

Run via cron/timer:  prof worker gmail-scan [--dry-run]
Requires: pip install profdash[gmail]  +  prof setup-gmail
"""

import argparse
import re
import sqlite3
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

from .. import paths
from ..gmail import client as gmail_client
from ..gmail.client import GmailUnavailable
from ..profile import Profile, SelfFilter, load_profile

# The statuses we scan for
SCAN_STATUSES = ("contacted", "no_response")

# Statuses we classify into
VALID_CLASSIFICATIONS = [
    "replied_template",
    "replied_interested",
    "replied_no_funding",
    "replied_rejected",
    "conditional_accept",
    "needs_follow_up",
]

# Config key for last scan timestamp
LAST_SCAN_KEY = "gmail_last_scan_at"

# How far back to look for sent emails when backfilling addresses
BACKFILL_SENT_DAYS = 30

# Generic subject patterns that indicate non-outreach mail.
# Extend via profile [self_filter] skip_subject_patterns.
DEFAULT_SKIP_SUBJECT_PATTERNS = [
    r"visa", r"housing", r"hostel", r"room reservation", r"work exchange",
    r"deferral", r"ticket confirmation", r"unsubscribe", r"mailing list",
    r"fwd:", r"blocked", r"test",
]


def _self_filter(profile: Profile | None) -> SelfFilter:
    return profile.self_filter if profile else SelfFilter()


def _skip_subject_regexes(profile: Profile | None) -> list[re.Pattern]:
    pats = DEFAULT_SKIP_SUBJECT_PATTERNS + _self_filter(profile).skip_subject_patterns
    out = []
    for p in pats:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


def _is_self_address(email_addr: str, profile: Profile | None) -> bool:
    """True when an address looks like YOURS, not a professor's."""
    sf = _self_filter(profile)
    e = email_addr.lower()
    for d in sf.domains:
        if d and e.endswith("@" + d.lower().lstrip("@")):
            return True
    for tok in sf.name_tokens:
        if tok and tok in e:
            return True
    return False


# ── Sent-email backfill (extract professor emails from To: headers) ─────


def gmail_sent_search(after_date: str, profile: Profile | None = None) -> list[dict]:
    """Search sent emails for To/Subject/Date headers. Returns list of
    {id, to, subject, date}."""
    try:
        service = gmail_client.get_service(
            (profile.gmail.token_path or None) if profile else None)
    except GmailUnavailable as e:
        log(f"  Gmail unavailable: {e}")
        return []

    query = f"in:sent after:{after_date}"
    try:
        msg_ids = gmail_client.search_message_ids(service, query, max_results=200)
    except Exception as e:
        log(f"  Gmail sent search error: {e}")
        return []

    output = []
    for mid in msg_ids:
        try:
            msg_data = service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["To", "Subject", "Date"]).execute()
        except Exception:
            continue
        headers = {h["name"].lower(): h["value"]
                   for h in msg_data.get("payload", {}).get("headers", [])}
        output.append({
            "id": mid,
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
        })
    return output


def match_email_to_professor(conn, email_addr: str) -> Optional[str]:
    """Try to match a Gmail To: address to a professor in the DB.

    Strategy:
    1. Extract last name from the To: display name (e.g. "Prof. Shang-En Huang")
    2. Search professors by last name (case-insensitive)
    3. If exactly one match, return the professor id
    4. If multiple matches, try institution domain matching
    5. If still ambiguous, skip (don't guess)
    """
    # Parse display name and email
    display_name = ""
    match = re.search(r'"?([^"<]+)"?\s*<', email_addr)
    if match:
        display_name = match.group(1).strip()

    # Extract email domain for institution matching
    domain_match = re.search(r"@([\w.-]+)", email_addr)
    domain = domain_match.group(1).lower() if domain_match else ""

    # Try to get last name from display name, or fallback to email local part
    if display_name:
        parts = display_name.replace("Prof.", "").replace("Dr.", "").strip().split()
        last_name = parts[-1].lower() if parts else ""
    else:
        local = email_addr.split("@")[0].lower()
        # Use the part after the last dot or the whole thing
        last_name = local.split(".")[-1] if "." in local else local

    if not last_name or len(last_name) < 2:
        return None

    # Search by last name
    rows = conn.execute(
        "SELECT id, professor FROM professors WHERE lower(professor) LIKE ?",
        (f"%{last_name}%",)
    ).fetchall()

    if len(rows) == 1:
        return rows[0]["id"]

    if len(rows) > 1:
        # Try institution domain matching
        # Map common domains to university keywords
        domain_parts = domain.replace("www.", "").split(".")
        inst_keywords = [p for p in domain_parts if len(p) > 3 and p not in ("ac", "edu", "com", "org")]

        for row in rows:
            prof_id = row["id"].lower()
            for kw in inst_keywords:
                if kw in prof_id:
                    return row["id"]

    return None  # Ambiguous or no match — skip


def backfill_emails_from_sent(conn, dry_run: bool = False,
                              profile: Profile | None = None) -> int:
    """Search recent sent emails, extract To: addresses, match to DB
    professors, and update their email + last_contacted_at if missing.

    Returns count of professors updated.
    """
    after = (datetime.now() - timedelta(days=BACKFILL_SENT_DAYS)).strftime("%Y/%m/%d")

    log(f"Backfilling emails from sent Gmail (after {after})...")
    sent_msgs = gmail_sent_search(after, profile)
    if not sent_msgs:
        log("  No sent emails found.")
        return 0

    skip_regexes = _skip_subject_regexes(profile)

    # Extract email addresses from To: headers
    seen = set()
    candidates = []
    for msg in sent_msgs:
        to = msg.get("to", "")
        subject = msg.get("subject", "")
        date = msg.get("date", "")

        # Skip non-outreach subjects (generic list + your profile's patterns)
        if any(rx.search(subject or "") for rx in skip_regexes):
            continue
        # Skip your own domains (e.g. university internal mail)
        if any(d and d.lower() in to.lower()
               for d in _self_filter(profile).domains):
            continue

        # Extract email addresses
        emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", to)
        for email in emails:
            if _is_self_address(email, profile):
                continue
            if email.lower() in seen:
                continue
            seen.add(email.lower())
            candidates.append((email.lower(), to, subject, date))

    log(f"  Found {len(candidates)} unique recipient address(es) to match.")

    updated = 0
    for email_addr, raw_to, subject, date in candidates:
        prof_id = match_email_to_professor(conn, raw_to)
        if not prof_id:
            continue

        # Check if professor already has this email
        row = conn.execute(
            "SELECT email FROM professors WHERE id = ?", (prof_id,)
        ).fetchone()
        if row and row["email"] == email_addr:
            continue  # Already has the right email

        # Parse the date from email header
        contacted_date = None
        try:
            dt = parsedate_to_datetime(date)
            contacted_date = dt.strftime("%Y-%m-%d")
        except Exception:
            contacted_date = datetime.now().strftime("%Y-%m-%d")

        if dry_run:
            log(f"  DRY RUN: would set {prof_id} email={email_addr}, last_contacted={contacted_date}")
        else:
            conn.execute(
                "UPDATE professors SET email = ?, last_contacted_at = COALESCE(last_contacted_at, ?) WHERE id = ?",
                (email_addr, contacted_date, prof_id)
            )
            conn.commit()

        prof_name = conn.execute(
            "SELECT professor FROM professors WHERE id = ?", (prof_id,)
        ).fetchone()["professor"]
        log(f"  ✓ {prof_name} -> {email_addr} (from: {subject[:50]})")
        updated += 1

    if updated == 0:
        log("  No new emails to backfill.")
    else:
        log(f"  Backfilled {updated} professor email(s).")

    return updated


# ── DB helpers (inline, no dashboard import needed for cron) ────────────


def connect(db_path=None):
    p = str(db_path or paths.find_db())
    conn = sqlite3.connect(p, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def get_professors_to_scan(conn) -> list[dict]:
    """Get professors with status in SCAN_STATUSES and a non-null email."""
    rows = conn.execute(
        """SELECT id, professor, email, last_contacted_at, status
           FROM professors
           WHERE status IN (?, ?)
             AND email IS NOT NULL
             AND email != ''
           ORDER BY professor""",
        SCAN_STATUSES,
    ).fetchall()
    return [dict(r) for r in rows]


def get_config(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_config(conn, key: str, value: str):
    conn.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
    )


def insert_status_history(
    conn, professor_id: str, status: str, note: str, gmail_message_id: str = ""
):
    """Insert a status_history row with confirmed=0 (pending human review).

    gmail_message_id is stored for dedup — prevents the same Gmail message
    from producing duplicate suggestions on re-scan.
    """
    conn.execute(
        """INSERT INTO status_history (professor_id, at, status, note, confirmed, rejected, gmail_message_id)
           VALUES (?, datetime('now'), ?, ?, 0, 0, ?)""",
        (professor_id, status, note, gmail_message_id or None),
    )


def already_processed(conn, gmail_message_id: str) -> bool:
    """Check if a status_history row already exists for this Gmail message.

    Returns True if any row (confirmed, rejected, or still pending) exists
    for this message ID — meaning we've already seen this email and should
    not create a new suggestion from it.
    """
    if not gmail_message_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM status_history WHERE gmail_message_id = ? LIMIT 1",
        (gmail_message_id,),
    ).fetchone()
    return row is not None


# ── Gmail API ───────────────────────────────────────────────────────────


def gmail_search(from_email: str, after_date: str,
                 profile: Profile | None = None) -> list[dict]:
    """Search Gmail for messages from from_email after after_date.

    Returns list of {id, subject, snippet, body, date, from}.
    """
    try:
        service = gmail_client.get_service(
            (profile.gmail.token_path or None) if profile else None)
    except GmailUnavailable as e:
        log(f"    Gmail unavailable: {e}")
        return []

    query = f"from:{from_email} after:{after_date}"
    try:
        msg_ids = gmail_client.search_message_ids(service, query, max_results=20)
    except Exception as e:
        log(f"    Gmail search failed: {e}")
        return []

    output = []
    for mid in msg_ids:
        try:
            msg = gmail_client.get_message(service, mid)
        except Exception:
            continue
        output.append({
            "id": msg["id"],
            "subject": msg["headers"].get("subject", ""),
            "from": msg["headers"].get("from", ""),
            "date": msg["headers"].get("date", ""),
            "snippet": msg["body_text"][:200],
            "body": msg["body_text"][:3000],  # Truncate very long emails
        })
    return output


# ── Reply classification ────────────────────────────────────────────────


def classify_reply(subject: str, body: str, snippet: str) -> tuple[str, str, str]:
    """Classify a reply into a status with confidence and a short quote.

    Uses keyword/pattern matching first, then falls back to a more nuanced
    analysis. Returns (status, confidence, note).

    Low confidence is a valid, expected output — never force-fit.
    """
    # Strip quoted reply text — only classify the professor's actual words,
    # not the original email they're replying to. Quoted lines start with >,
    # and many clients prepend "On <date> <name> wrote:" before the quote.
    reply_only = body
    # Remove everything from "On ... wrote:" onward (common quote header)
    quote_header = re.search(r"\nOn .{0,80}wrote:\n", reply_only)
    if quote_header:
        reply_only = reply_only[:quote_header.start()]
    # Remove lines starting with > (quoted text)
    reply_only = "\n".join(
        line for line in reply_only.split("\n") if not line.strip().startswith(">")
    )

    text = f"{subject}\n{reply_only}".lower()
    # Clean up for matching
    text_clean = re.sub(r"\s+", " ", text)
    snippet_lower = snippet.lower() if snippet else ""

    # Extract the most relevant quote from the reply-only text (not full body)
    body_clean = re.sub(r"\s+", " ", reply_only).strip()
    if not body_clean:
        # Fallback: if stripping removed everything, use the original body
        body_clean = re.sub(r"\s+", " ", body).strip()
    quote = body_clean[:250]
    if len(body_clean) > 250:
        quote += "..."

    # ── Strong signals (high confidence) ────────────────────────────────

    # No funding — check BEFORE rejection, because "unfortunately I don't
    # have funding" is more precisely no_funding than a generic rejection.
    funding_patterns = [
        r"(?:no|lack (?:of|ing)|without)\s+(?:funding|financial|support|scholarship|stipend)",
        r"(?:not? (?:have|has|having))\s+(?:funding|financial|support)",
        r"(?:funded|funding).{0,30}(?:position|slot).{0,20}(?:not|no|filled|available)",
        r"(?:self.?funded|self.?support|own funding|external funding)",
        r"(?:bring your own|your own funding|outside funding)",
    ]
    for pattern in funding_patterns:
        if re.search(pattern, text_clean):
            return "replied_no_funding", "high", f"No funding mentioned: \"{quote}\""

    # Rejected: clear negative
    rejection_patterns = [
        r"not (?:accepting|taking|currently taking)\s+(?:new\s+)?(?:phd|graduate|student)",
        r"(?:no|don'?t have|do not have)\s+(?:any\s+)?(?:open|available)\s+(?:position|slot|funding|spot)",
        r"(?:un)?fortunately.{0,30}(?:not|cannot|can'?t)\s+(?:accept|take|offer|supervise)",
        r"position(s)?\s+(?:are\s+)?(?:not\s+)?(?:available|filled|full)",
        r"we are (?:not|unable)",
        r"(?:wish|good luck)\s+(?:you|with your)\s+(?:search|application)",
        r"good luck\b.{0,40}(?:best|wish|search|future)",
        r"best wishes\b.{0,20}(?:search|future|endeavor)",
        r"\bstrict(?:ly)? (?:required|necessar)\b.{0,50}(?:good luck|best wishes|wish)",
        r"regret(?:s|fully)?\s+(?:to inform|that)",
        r"(?:not|isn'?t)\s+a (?:good|suitable)\s+fit",
        r"thank(s| you).{0,40}(?:but|however|unfortunately)",
        r"(?:decline|passing on)\s+(?:your|this)",
        r"(?:not|no longer)\s+(?:my|our)\s+(?:area|field|research area)",
    ]
    for pattern in rejection_patterns:
        if re.search(pattern, text_clean):
            return "replied_rejected", "high", f"Rejection: \"{quote}\""

    # Template / generic auto-reply
    template_patterns = [
        r"(?:auto|automatic|out of office|ooo|away|vacation|travel)",
        r"(?:thank you for your (?:email|interest|inquiry|message))",
        r"(?:i (?:will|shall) (?:get back|respond|reply))",
        r"(?:due to (?:the )?(?:high )?(?:volume|number))",
        r"(?:this is an? (?:automatic|automated))",
        r"(?:do.?not reply|noreply|no-reply)",
        r"(?:currently (?:away|traveling|unavailable))",
    ]
    for pattern in template_patterns:
        if re.search(pattern, text_clean):
            return "replied_template", "high", f"Auto/template reply: \"{quote}\""

    # Conditional accept
    conditional_patterns = [
        r"(?:if you|provided (?:that|you)|assuming you).{0,40}(?:would be|could be|interested)",
        r"(?:happy|willing|glad)\s+to\s+(?:discuss|chat|meet|talk).{0,30}(?:if|provided|assuming)",
        r"(?:interest(ed)?\s+in\s+(?:your|this)).{0,30}(?:could|would|might)",
        r"(?:conditional|contingent|subject to).{0,20}(?:accept|admission|funding)",
        r"(?:can|could)\s+(?:we|i)\s+(?:schedule|arrange|set up)\s+(?:a\s+)?(?:call|meeting|chat)",
        r"(?:let(?:'?s| us)\s+(?:schedule|arrange|meet|talk|chat|discuss))",
    ]
    for pattern in conditional_patterns:
        if re.search(pattern, text_clean):
            return "conditional_accept", "medium", f"Possible conditional interest: \"{quote}\""

    # Interested: strong positive signals
    interest_patterns = [
        r"(?:very |quite |really )?(?:interested|excited).{0,40}(?:in (?:your|this|supervising|working with|having))",
        r"(?:very |quite |really )?(?:interested|excited).{0,30}(?:to (?:learn|hear|discuss|work))",
        r"(?:would (?:love|like) to (?:discuss|hear|learn|explore|supervise|work))",
        r"(?:strong (?:candidate|fit|match|background))",
        r"(?:send (?:me|us) your (?:cv|resume|paper|writing))",
        r"(?:tell me more|more (?:detail|information) about)",
        r"(?:impressive (?:background|profile|work|publication))",
    ]
    for pattern in interest_patterns:
        if re.search(pattern, text_clean):
            return "replied_interested", "high", f"Positive interest: \"{quote}\""

    # ── Medium signals ──────────────────────────────────────────────────

    # Needs follow-up: asks a question or requests info
    followup_patterns = [
        r"\?",
        r"(?:could you|can you|please)\s+(?:send|share|provide|tell|explain|clarify)",
        r"(?:i (?:have|wonder|would like to know))",
        r"(?:what (?:is|are|about|kind of))",
        r"(?:when (?:did|do|would|will))",
        r"(?:which|how (?:many|much|long|often))",
    ]
    for pattern in followup_patterns:
        if re.search(pattern, text_clean):
            return "needs_follow_up", "medium", f"Asks follow-up question: \"{quote}\""

    # Interested: weaker positive signals
    weak_interest = [
        r"(?:interest(?:ed|ing)?\s+(?:in|to)\s+(?:your|this|the))",
        r"(?:would (?:be )?(?:open|happy)\s+to)",
        r"(?:feel free to (?:send|reach|apply|contact))",
        r"(?:look(?:ing)? (?:forward|at))",
    ]
    for pattern in weak_interest:
        if re.search(pattern, text_clean):
            return "replied_interested", "medium", f"Possible interest: \"{quote}\""

    # ── Low-confidence fallback ─────────────────────────────────────────

    # If the body is very short, it's probably a brief acknowledgment
    if len(body_clean) < 50:
        return "needs_follow_up", "low", f"Brief reply (unclear intent): \"{quote}\""

    # If nothing matched, classify as needs_follow_up with low confidence
    return "needs_follow_up", "low", f"Ambiguous reply — review manually: \"{quote}\""


# ── Main scan logic ─────────────────────────────────────────────────────


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def compute_search_after(prof: dict, last_scan: Optional[str]) -> str:
    """Compute the 'after:' date for Gmail search.

    Uses the more recent of last_contacted_at or last_scan_at.
    Falls back to 30 days ago if neither exists.
    """
    dates_to_compare = []

    if prof.get("last_contacted_at"):
        dates_to_compare.append(prof["last_contacted_at"])

    if last_scan:
        dates_to_compare.append(last_scan)

    if not dates_to_compare:
        # Default: 30 days ago
        from datetime import timedelta

        d = datetime.now() - timedelta(days=30)
        return d.strftime("%Y/%m/%d")

    # Parse dates and pick the most recent
    parsed = []
    for d in dates_to_compare:
        try:
            # Handle various formats
            if "T" in d:
                parsed.append(datetime.fromisoformat(d.replace("Z", "+00:00")))
            else:
                parsed.append(datetime.strptime(d, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            try:
                parsed.append(datetime.strptime(d, "%Y-%m-%d"))
            except ValueError:
                continue

    if not parsed:
        from datetime import timedelta

        d = datetime.now() - timedelta(days=30)
        return d.strftime("%Y/%m/%d")

    most_recent = max(parsed)
    return most_recent.strftime("%Y/%m/%d")


def extract_email_address(from_header: str) -> str:
    """Extract email address from a From header like 'Name <email@example.com>'."""
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).lower().strip()
    # If no angle brackets, the whole thing might be an email
    return from_header.lower().strip()


def scan(dry_run: bool = False, profile: Profile | None = None,
         db_path=None) -> bool:
    """Run one scan pass. Returns True if Gmail was reachable."""
    profile = profile or load_profile()
    conn = connect(db_path)

    # Step 1: Backfill professor emails from recent sent Gmail
    backfill_emails_from_sent(conn, dry_run=dry_run, profile=profile)

    # Step 2: Get professors to scan (now includes any newly backfilled ones)
    last_scan = get_config(conn, LAST_SCAN_KEY)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    professors = get_professors_to_scan(conn)
    if not professors:
        log("No professors to scan (none with contacted/no_response status and email).")
        conn.close()
        return True

    log(f"Scanning Gmail for {len(professors)} professor(s)...")
    if last_scan:
        log(f"Last scan: {last_scan}")
    else:
        log("First scan — using last_contacted_at or 30-day default.")

    total_new = 0
    gmail_ok = True

    for prof in professors:
        prof_id = prof["id"]
        prof_name = prof["professor"]
        email = prof["email"]
        after_date = compute_search_after(prof, last_scan)

        log(f"  {prof_name} <{email}> (after: {after_date})")

        # Search Gmail
        messages = gmail_search(email, after_date, profile)

        if not messages:
            log(f"    No new messages found.")
            continue

        log(f"    Found {len(messages)} message(s)")

        for msg in messages:
            msg_id = msg.get("id", "")
            msg_from = extract_email_address(msg.get("from", ""))
            msg_body = msg.get("body", "")
            msg_subject = msg.get("subject", "")
            msg_snippet = msg.get("snippet", "")

            # Verify the from address matches (case-insensitive)
            if msg_from != email.lower():
                log(
                    f"    Skipping message from {msg_from} (expected {email})"
                )
                continue

            # Dedup: skip if we already have a status_history row for this
            # specific Gmail message, regardless of confirmation state.
            if already_processed(conn, msg_id):
                log(f"    Skipping message {msg_id} (already processed)")
                continue

            # Classify
            suggested_status, confidence, note = classify_reply(
                msg_subject, msg_body, msg_snippet
            )

            full_note = f"[{confidence}] {note}"

            if dry_run:
                log(f"    DRY RUN: would insert -> {suggested_status} ({confidence})")
                log(f"      Note: {note[:100]}...")
            else:
                insert_status_history(
                    conn, prof_id, suggested_status, full_note, gmail_message_id=msg_id
                )
                conn.commit()
                total_new += 1
                log(
                    f"    Inserted: {suggested_status} ({confidence}) for {prof_name}"
                )

    # Update last scan time
    if not dry_run:
        set_config(conn, LAST_SCAN_KEY, now_str)
        conn.commit()
        log(f"Scan complete. {total_new} new suggestion(s). Last scan updated to {now_str}")
    else:
        log(f"DRY RUN complete. No changes made.")

    conn.close()
    return gmail_ok


def show_status(db_path=None):
    """Show scan status."""
    conn = connect(db_path)
    last_scan = get_config(conn, LAST_SCAN_KEY)
    professors = get_professors_to_scan(conn)
    pending = conn.execute(
        "SELECT COUNT(*) FROM status_history WHERE confirmed = 0 AND (rejected = 0 OR rejected IS NULL)"
    ).fetchone()[0]

    print(f"Last scan: {last_scan or 'never'}")
    print(f"Professors to scan: {len(professors)}")
    print(f"Pending confirmations: {pending}")
    conn.close()


def main(db_path=None, profile: Profile | None = None):
    ap = argparse.ArgumentParser(description="Scan Gmail for professor replies")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be inserted without writing")
    ap.add_argument("--status", action="store_true", help="Show scan status")
    args = ap.parse_args()

    if args.status:
        show_status(db_path)
        return

    try:
        scan(dry_run=args.dry_run, profile=profile, db_path=db_path)
    except GmailUnavailable as e:
        print(f"Gmail unavailable: {e}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
