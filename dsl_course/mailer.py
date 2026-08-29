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
import time
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


# Graph answers a throttled or briefly-unhealthy request with one of these and, on a 429,
# a `Retry-After`. Three attempts total: enough to ride out a throttle window, few enough
# that one bad recipient cannot eat the job's timeout.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_SEND_ATTEMPTS = 3
_RETRY_AFTER_DEFAULT = 5.0  # when the header is absent or unreadable
_RETRY_AFTER_CAP = 60.0  # a header we cannot vet must not park the whole batch


def _response_headers(raw) -> dict[str, str]:
    """A response's headers as a lower-cased dict (HTTP header names are case-insensitive,
    and `Retry-After` arrives spelled however the server felt like spelling it)."""
    try:
        return {k.lower(): v for k, v in raw.items()}
    except AttributeError:
        return {}


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes, dict]:
    """POST and return (status, body, response headers); network/HTTP errors come back as
    a status + body, so no caller has to handle an exception to see a failure."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), _response_headers(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), _response_headers(exc.headers)
    except urllib.error.URLError as exc:
        return 0, str(exc.reason).encode(), {}


def retry_after_seconds(headers: dict[str, str]) -> float:
    """How long `Retry-After` asks us to wait, in seconds - capped, and defaulted when the
    header is missing or is the HTTP-date form we do not parse."""
    raw = (headers.get("retry-after") or "").strip()
    try:
        wait = float(raw)
    except ValueError:
        return _RETRY_AFTER_DEFAULT
    return min(max(wait, 0.0), _RETRY_AFTER_CAP)


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
    status, raw, _headers = _post(
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
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
        status, _raw, response_headers = _post(url, payload, headers)
        if status in (200, 202):
            return True
        if status in _RETRY_STATUSES and attempt < _MAX_SEND_ATTEMPTS:
            # A throttle (429) or a brief 5xx is not a bad recipient - it is Graph asking
            # to be asked again. Un-retried, one throttled minute silently cost a whole
            # cohort their enrolment codes, and the log said only "failed (429)".
            wait = retry_after_seconds(response_headers)
            log(
                f"  [wait] send to {mask_email(to)} got {status}, "
                f"retry {attempt}/{_MAX_SEND_ATTEMPTS - 1} in {wait:g}s"
            )
            time.sleep(wait)
            continue
        # Status only: a Graph error body echoes the request, recipient included.
        log_err(f"send to {mask_email(to)} failed ({status})")
        return False
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


DEFAULT_SMTP_PORT = 587


def smtp_port_from_env() -> int:
    """`SMTP_PORT`, or the default when it is unset, blank, or not a number.

    Blank is the case that mattered: an Actions `env:` block always DEFINES the variable,
    so an unconfigured `SMTP_PORT` arrives as `""` rather than absent - and `int("")` is a
    traceback out of the mail step, before a single message is built."""
    raw = (os.environ.get("SMTP_PORT") or "").strip()
    if not raw:
        return DEFAULT_SMTP_PORT
    try:
        return int(raw)
    except ValueError:
        log_err(f"SMTP_PORT is not a number ({raw!r}) - using {DEFAULT_SMTP_PORT}")
        return DEFAULT_SMTP_PORT


def smtp_config_from_env() -> SMTPConfig | None:
    """Build the SMTP config from env, or None if the required secrets are unset."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (host and user and password):
        return None
    return SMTPConfig(
        host=host,
        port=smtp_port_from_env(),
        user=user,
        password=password,
        from_addr=os.environ.get("SMTP_FROM") or user,
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


SAMPLE_HEADER = "--- sample (placeholders) ---"


def send_bulk(
    messages: list[Message], dry_run: bool = False, sample: str | None = None
) -> int:
    """Preview (dry_run) or send a batch. Returns the count previewed/sent.

    dry_run lists masked recipients + subjects and sends nothing. Never a REAL body: the
    enrolment-code email carries the student's name and a live credential, and this runs
    in a public repo whose Actions log anyone can read.

    `sample` is the one thing a masked list cannot give a reviewer - the wording. It is a
    body the CALLER rendered from placeholders (`<name>`, `<code>`), never one of
    `messages`, and it is printed once, under `SAMPLE_HEADER`, so faculty can proof-read
    the email before a real send. Otherwise the transport is chosen by whichever secrets
    are configured (Graph preferred, SMTP fallback)."""
    if dry_run:
        for to, subject, _body in messages:
            log(f"  would send -> {mask_email(to)}: {subject}")
        if sample:
            log(SAMPLE_HEADER)
            log(sample)
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
