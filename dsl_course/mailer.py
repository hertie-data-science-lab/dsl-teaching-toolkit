"""dsl-course mailer -- send templated per-recipient email (preview-then-send), transport-agnostic.

The reusable, previewable replacement for the Excel -> Power Automate -> Outlook mail-merge:
build one message per roster row, print them all for review (`dry_run`), then send. Shared by
enrolment-code distribution and grade notifications - both just hand `send_bulk` a list of messages.

Two transports, chosen by whichever secrets are present (Graph preferred):
    Microsoft Graph (application auth):
        GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_SENDER
    SMTP (fallback, e.g. if the tenant still allows SMTP AUTH):
        SMTP_HOST, SMTP_USER, SMTP_PASSWORD  (+ optional SMTP_PORT=587, SMTP_FROM=user)

`GRAPH_SENDER` is the mailbox to send as (a shared mailbox, e.g. datasciencelab@hertie-school.org);
the Entra app needs the Mail.Send application permission (admin-consented), ideally scoped to that
one mailbox via an Application Access Policy. Until either transport is configured, `dry_run`
previews everything offline.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from .utils import log, log_err, log_ok

# A single message: (recipient, subject, body).
Message = tuple[str, str, str]

_AUTHORITY = "https://login.microsoftonline.com"
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"


# --------------------------------------------------------------------------- Microsoft Graph


@dataclass
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    sender: str


def graph_config_from_env() -> GraphConfig | None:
    """Build the Graph config from env, or None if any required secret is unset."""
    tenant = os.environ.get("GRAPH_TENANT_ID")
    client_id = os.environ.get("GRAPH_CLIENT_ID")
    secret = os.environ.get("GRAPH_CLIENT_SECRET")
    sender = os.environ.get("GRAPH_SENDER")
    if not (tenant and client_id and secret and sender):
        return None
    return GraphConfig(tenant, client_id, secret, sender)


def mask_email(addr: str) -> str:
    """`a***@domain` - enough to tell two recipients apart in a run log, not enough to
    identify either. Every workflow runs in a PUBLIC repo, so its log is world-readable."""
    local, _, domain = addr.partition("@")
    return f"{local[:1]}***@{domain}" if domain else f"{local[:1]}***"


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """POST and return (status, body); network/HTTP errors come back as a status + body."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode()


def _graph_token(cfg: GraphConfig) -> str | None:
    """A client-credentials access token for Graph, or None on failure."""
    url = f"{_AUTHORITY}/{cfg.tenant_id}/oauth2/v2.0/token"
    body = urllib.parse.urlencode(
        {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "scope": _SCOPE,
            "grant_type": "client_credentials",
        }
    ).encode()
    status, raw = _post(
        url, body, {"Content-Type": "application/x-www-form-urlencoded"}
    )
    if status != 200:
        log_err(
            f"Graph token request failed ({status}): {raw[:200].decode(errors='replace')}"
        )
        return None
    return json.loads(raw).get("access_token")


def _graph_send_one(
    cfg: GraphConfig, token: str, to: str, subject: str, body: str
) -> bool:
    """Send one message via `users/{sender}/sendMail`. Returns True on 200/202."""
    url = f"{_GRAPH}/users/{urllib.parse.quote(cfg.sender)}/sendMail"
    payload = json.dumps(
        {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": False,
        }
    ).encode()
    status, _raw = _post(
        url,
        payload,
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if status in (200, 202):
        return True
    # Status only: a Graph error body echoes the request, recipient included.
    log_err(f"send to {mask_email(to)} failed ({status})")
    return False


def _send_via_graph(cfg: GraphConfig, messages: list[Message]) -> int:
    """Send the whole batch on one token. Returns the number actually sent.

    A failed token request raises rather than returning 0: nothing was sent AND nothing
    could be, which is a transport failure, not an empty batch - the two must not read
    the same to the caller."""
    token = _graph_token(cfg)
    if token is None:
        raise RuntimeError(
            "Microsoft Graph token request failed - check the GRAPH_* secrets. Nothing sent."
        )
    sent = 0
    for to, subject, body in messages:
        if _graph_send_one(cfg, token, to, subject, body):
            log_ok(f"sent -> {mask_email(to)}")
            sent += 1
    return sent


# ----------------------------------------------------------------------------------- SMTP


@dataclass
class SMTPConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str


def smtp_config_from_env() -> SMTPConfig | None:
    """Build the SMTP config from env, or None if the required secrets are unset."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (host and user and password):
        return None
    return SMTPConfig(
        host=host,
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=user,
        password=password,
        from_addr=os.environ.get("SMTP_FROM", user),
    )


def _send_via_smtp(cfg: SMTPConfig, messages: list[Message]) -> int:
    """One connect + login reused for the whole batch; a bad recipient is logged, not fatal."""
    sent = 0
    try:
        with smtplib.SMTP(cfg.host, cfg.port) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(cfg.user, cfg.password)
            for to, subject, body in messages:
                msg = EmailMessage()
                msg["From"], msg["To"], msg["Subject"] = cfg.from_addr, to, subject
                msg.set_content(body)
                try:
                    server.send_message(msg)
                    log_ok(f"sent -> {mask_email(to)}")
                    sent += 1
                except smtplib.SMTPException as exc:
                    log_err(f"send to {mask_email(to)} failed: {type(exc).__name__}")
    except (smtplib.SMTPException, OSError) as exc:
        log_err(f"SMTP connection failed: {exc}")
    return sent


# ---------------------------------------------------------------------------------- public


def send_bulk(messages: list[Message], dry_run: bool = False) -> int:
    """Preview (dry_run) or send a batch. Returns the count previewed/sent.

    dry_run lists masked recipients + subjects and sends nothing. Never the body: the
    enrolment-code email carries the student's name and a live credential, and this runs
    in a public repo whose Actions log anyone can read. Otherwise the transport is chosen
    by whichever secrets are configured (Graph preferred, SMTP fallback)."""
    if dry_run:
        for to, subject, _body in messages:
            log(f"  would send -> {mask_email(to)}: {subject}")
        log_ok(f"DRY-RUN previewed {len(messages)} message(s) - nothing sent")
        return len(messages)

    graph = graph_config_from_env()
    if graph is not None:
        return _send_via_graph(graph, messages)
    smtp = smtp_config_from_env()
    if smtp is not None:
        return _send_via_smtp(smtp, messages)
    log_err(
        "No mail transport configured - set the GRAPH_* secrets (preferred) or the "
        "SMTP_* secrets. Nothing sent."
    )
    return 0
