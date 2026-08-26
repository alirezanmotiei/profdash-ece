"""Gmail API access for profdash workers.

Token format (~/.config/profdash/google_token.json by default):
    {"token": "...", "refresh_token": "...", "token_uri": "https://oauth2...",
     "client_id": "...", "client_secret": "...", "scopes": [...]}

Create it with:  prof setup-gmail --client-secret ~/client_secret.json
Requires the "gmail" optional dependency group: pip install profdash[gmail].
"""
from __future__ import annotations

import base64
import json
from email.mime.text import MIMEText
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailUnavailable(RuntimeError):
    """Raised when Gmail features can't run (no deps, no token, API error)."""


def _require_google_libs():
    try:
        from googleapiclient.discovery import build  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google.auth.transport.requests import Request  # noqa: F401
    except ImportError as e:
        raise GmailUnavailable(
            "Google libraries not installed. Run: pip install profdash[gmail]"
        ) from e


def load_token(token_path: str | Path | None = None) -> dict:
    p = Path(token_path).expanduser() if token_path else _default_token_path()
    if not p.exists():
        raise GmailUnavailable(
            f"Gmail token not found at {p}. Run: prof setup-gmail")
    with open(p) as f:
        return json.load(f)


def save_token(data: dict, token_path: str | Path | None = None) -> Path:
    p = Path(token_path).expanduser() if token_path else _default_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
    return p


def _default_token_path() -> Path:
    from .. import paths
    return paths.default_token_path()


def get_service(token_path: str | Path | None = None):
    """Build an authenticated gmail v1 service, refreshing if needed."""
    _require_google_libs()
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    tp = Path(token_path).expanduser() if token_path else _default_token_path()
    data = load_token(tp)
    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
    )
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        data["token"] = creds.token
        if getattr(creds, "expiry", None):
            data["expiry"] = creds.expiry.isoformat()
        save_token(data, tp)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# --- high-level operations ---------------------------------------------------


def search_message_ids(service, query: str, *, max_results: int = 50) -> list[str]:
    resp = service.users().messages().list(
        userId="me", q=query, maxResults=max_results).execute()
    return [m["id"] for m in resp.get("messages", [])]


def get_message(service, msg_id: str) -> dict:
    """Full message dict: id, threadId, headers (dict), body_text."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full").execute()
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h["value"]

    body_text = ""
    parts = [msg.get("payload", {})]
    while parts:
        part = parts.pop(0)
        if part.get("parts"):
            parts.extend(part["parts"])
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime.startswith("text/"):
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                body_text = decoded
                break
            if not body_text:
                body_text = decoded
    if not body_text and msg.get("snippet"):
        body_text = msg["snippet"]
    return {"id": msg["id"], "thread_id": msg.get("threadId"),
            "headers": headers, "body_text": body_text}


def create_draft(service, to: str, subject: str, body: str) -> str:
    """Create a Gmail draft; returns the draft id."""
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"]


# --- first-time setup ------------------------------------------------------------


def run_oauth_flow(client_secret_path: str | Path,
                   token_path: str | Path | None = None) -> Path:
    """Interactive OAuth consent flow. Saves token JSON and returns its path."""
    _require_google_libs()
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(Path(client_secret_path).expanduser()), SCOPES)
    creds = flow.run_local_server(port=0)
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    return save_token(data, token_path)
