# Gmail integration

profdash's Gmail features are **optional**:

- **Reply scanning** — classifies replies from contacted professors and
  queues suggestions for your confirmation
- **Email drafting** — outreach drafts land in your Gmail Drafts folder
- **Address backfill** — extracts professor addresses from your sent mail

Nothing is ever *sent* automatically. Drafts wait for you; status changes
wait for your confirmation.

## One-time setup

1. **Google Cloud project + OAuth client**
   - Go to <https://console.cloud.google.com/apis/credentials>
   - Create a project (any name), then *Create credentials → OAuth client ID → Desktop app*
   - Download the client secret JSON
   - Enable the Gmail API for the project
     (<https://console.cloud.google.com/apis/library/gmail.googleapis.com>)
   - If you're in the "Testing" publishing status, add your own Google
     account as a test user (OAuth consent screen → Audience)

2. **Install + authorize**

   ```bash
   pip install profdash[gmail]
   prof setup-gmail --client-secret ~/Downloads/client_secret_xxx.json
   ```

   A browser window opens; approve the consent. The token is saved to
   `~/.config/profdash/google_token.json` (override in `profile.toml` →
   `[gmail] token_path`).

   Scopes granted: `gmail.readonly`, `gmail.modify`, `gmail.compose`.

3. **Test**

   ```bash
   prof worker gmail-scan --dry-run
   ```

## How reply scanning works

Each pass:

1. **Backfill** — reads To: headers of sent mail from the last 30 days,
   matches addresses to professors (last name + institution domain),
   stamps `email` + `last_contacted_at`. Skips your own addresses using
   `[self_filter]` in `profile.toml` and generic non-outreach subjects.
2. **Scan** — for every professor with status `contacted`/`no_response`,
   searches Gmail for new messages since `max(last_contacted_at, last
   scan)`. Each reply is classified by rules (regex pattern families, no
   LLM) into:

   | Suggestion | Typical trigger |
   |---|---|
   | `replied_no_funding` | "no funding for a new student" |
   | `replied_rejected` | "not taking students", "good luck with your search" |
   | `replied_template` | auto-replies, "due to the volume of emails" |
   | `replied_interested` | "send me your CV", "impressive background" |
   | `conditional_accept` | "happy to chat if ..." |
   | `needs_follow_up` | questions, ambiguity, anything unclear |

   with a confidence level (high/medium/low). Low confidence is a valid,
   expected outcome — the classifier never force-fits.

3. **Queue** — suggestions are inserted as *unconfirmed* history rows.
   Review them on the dashboard's **Confirmations** page: confirm, edit,
   or reject each one. Only confirmation changes the professor's status.

Deduplication is by Gmail message id — re-scans never double-suggest.

## Scheduling

See [self-hosting.md](self-hosting.md) — typically every 30–60 minutes.

## Revoking access

Remove the project in Google Cloud (or revoke at
<https://myaccount.google.com/permissions>) and delete
`~/.config/profdash/google_token.json`.
