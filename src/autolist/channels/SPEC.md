---
module: channels
status: approved
approved_by: DEV (Or)
approved_at: 2026-10-30
phase: 0 (email connection + health check + cost alerts), 3 (full intake/outbound)
---

# channels

## Purpose
The only module that receives or sends messages. A channel adapter turns a provider's message into a `ChannelMessage` and sends `OutboundMessage`s. The rest of the system does not know the channel is email, so WhatsApp can be added later as another adapter.

## Files
| File | Phase | Responsibility |
|---|---|---|
| `base.py` | P0 | `Channel` protocol, `ChannelMessage`, `OutboundMessage`, `Attachment` models. |
| `email/gmail_watch.py` | P0 | Starts and renews `users.watch` on the agent mailbox to the Pub/Sub topic; validates push (OIDC); fetches history since last `historyId`. |
| `email/sender_auth.py` | P0 | Accepts a message only if sender ∈ allowlist **and** `Authentication-Results` has `dkim=pass` and `dmarc=pass` for the sender's domain. |
| `email/mail_reader.py` | P3 | Parses MIME into `ChannelMessage` (text, HTML→text, attachments with size/type limits, thread id, Message-ID). |
| `email/mail_sender.py` | P0 (plain text) / P3 (HTML preview) | Sends from the agent mailbox, in-thread when replying (`In-Reply-To`, `References`, `threadId`). Renders bodies from `agent/replies/*.md` templates only. |

## Interface
```python
class ChannelMessage(BaseModel):
    channel: Literal["email"]; message_id: str; thread_id: str
    sender: str; authenticated: bool; received_at: datetime
    subject: str | None; text: str; attachments: list[Attachment]

class OutboundMessage(BaseModel):
    channel: Literal["email"]; to: str; thread_id: str | None
    template: str            # file name in agent/replies, e.g. "cost_alert"
    variables: dict[str, str]
    attachments: list[Attachment] = []

class Channel(Protocol):
    def fetch_new(self) -> list[ChannelMessage]: ...
    def send(self, msg: OutboundMessage) -> str: ...   # returns provider message id
    def health(self) -> HealthStatus: ...
```

## Rules
- **Sender allowlist and owner address:** from `store.yaml` (`channels.email.allowed_senders`, `owner_address`).
- **Unauthenticated or non-allowlisted mail** is logged with sender and auth result only, never the body. It is labeled `autolist/rejected` and gets **no reply**, so the system cannot be used as a spam reflector.
- **Idempotency:** a Message-ID that has already been processed is ignored.
- **Outbound recipients:** only to `owner_address`. A template asking for any other recipient raises `RecipientNotAllowed`.
- **Every outbound email ends with the automated-agent signature** from `agent/replies/_signature.md`.
- **OAuth.**
  - The refresh token for the agent mailbox is stored in Secret Manager.
  - Scopes: `gmail.modify` only (read, label, send). No `gmail.settings.*`, no full-mailbox `mail.google.com`.
  - The OAuth app must be In production, not Testing, to avoid 7-day refresh-token expiry. That is recorded in decisions.
- **Health check (daily, Cloud Scheduler):**
  - refresh the token
  - call `users.getProfile`
  - verify the watch expiry is more than 24h away, renewing it if not
  - on failure, alert through Cloud Monitoring. Not by email, because email may be what's broken.

## Side effects
Gmail API calls. Pub/Sub ack. Label changes on the agent mailbox.

## Errors
`SenderRejected`, `RecipientNotAllowed`, `ChannelAuthError` (token revoked or expired), `TemplateMissing`, `AttachmentRejected`.

## Allowed dependencies
`autolist.core`, `google-api-python-client`, `google-auth`. Templates are read from the store repo through a small loader in this module (not `llm.prompt_loader`: replies are not prompts).

## Not responsible for
- Understanding message content (`intake`, `agent_runtime`).
- Deciding what to reply (`agent_runtime`).

## Acceptance criteria (P0)
1. `sender_auth` unit tests on recorded headers:
   - allowlisted sender with dkim and dmarc pass → accepted
   - same sender with `dkim=fail` → rejected
   - any other address with a pass → rejected
   - a spoofed `From` with a pass for a different domain → rejected
2. A live watch on the agent mailbox delivers a Pub/Sub push for a test email from the owner within 60s, and the history fetch returns exactly that message.
3. `mail_sender` sends `cost_alert` to the owner in plain text, with the signature. A test asserts any other recipient raises `RecipientNotAllowed`.
4. The health check runs on day 1 and again after day 8. The token still refreshes on day 8, which proves no 7-day expiry. Both results are recorded in decisions.
5. Logs contain no message bodies and no tokens (log-capture test).

## Implementation notes (P0-08 offline part, 2026-11-02)
- **Models location.** `ChannelMessage`, `OutboundMessage`, `Attachment` and `HealthStatus` live in `core/models.py`, not `base.py`. The SPEC table put them in `base.py`, but the overriding rule 3 in CLAUDE.md says modules exchange only core models, and `agent_runtime` will need them without importing `channels`. `base.py` keeps the `Channel` protocol.
- **`OutboundMessage.in_reply_to`.** Holds the Message-ID being answered, so a reply sets `In-Reply-To` and `References` (the SPEC requires in-thread replies).
- **Files added:**
  - `errors.py`
  - `templates.py`: reply-template loader. Front matter has `subject` and `variables`, rendering is strict, and `_signature.md` is appended to every body (templates never include it).
  - `email/gmail_client.py`: credentials from the two secrets, `ChannelAuthError` on refresh failure.
  - `email/health.py`
- **Sender auth.**
  - Only the topmost `Authentication-Results` header with authserv-id `mx.google.com` is trusted, so a header the sender added lower in the message is ignored.
  - DKIM must pass with `header.d`/`header.i` equal to the sender's domain, and DMARC must pass with `header.from` equal to it.
  - Exactly one `From` address is allowed.
- **Fetching.** History is read with `historyTypes=messageAdded`. Headers come with `format=metadata`; bodies are not fetched in P0 (that is `mail_reader`, P3).
- **Config added** (`store.yaml`): `secrets.gmail_oauth_client`, `channels.email.pubsub_topic`, `channels.email.pubsub_subscription`.
- **OAuth bootstrap.** `scripts/setup/gmail_oauth_bootstrap.py` runs once in Cloud Shell. It stores the client JSON and the refresh token directly in Secret Manager, after checking that the signed-in account is the agent mailbox.

## Change (2026-11-02, requested by DEV): right-to-left emails
- Every email is `multipart/alternative`: the plain text, plus the same text as HTML with `dir` and `lang` from `store.yaml` (`channels.email.text_direction`, `channels.email.language`).
- In the HTML part, text is escaped (no template or variable markup is passed through), and blank lines become paragraphs.
- Reason: the first live reply (P0-08) rendered left-to-right in Gmail.
