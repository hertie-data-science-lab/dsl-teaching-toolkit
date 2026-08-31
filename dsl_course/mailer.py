"""dsl-course mailer -- send templated per-recipient email (preview-then-send).

The reusable, previewable replacement for the Excel -> Power Automate -> Outlook mail-merge:
build one message per roster row, print them all for review (`dry_run`), then send. Shared by
enrolment-code distribution and grade notifications - both just hand `send_bulk` a list of messages.

One transport, Microsoft Graph (application auth, certificate credential):
    GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_CERT, GRAPH_SENDER

There is deliberately no SMTP fallback. The tenant disables SMTP AUTH, so the fallback could
never fire; keeping it meant a second, untested send path whose `EmailMessage` took a raw
roster cell as a header. Until the secrets are set, `dry_run` previews everything offline.

`GRAPH_SENDER` is the mailbox to send as (a shared mailbox, e.g. datasciencelab@hertie-school.org);
the Entra app needs the Mail.Send application permission (admin-consented), scoped to that one
mailbox via an Exchange application access policy.

`GRAPH_CLIENT_CERT` is ONE secret holding both halves of the app's certificate credential -
the PEM certificate followed by its unencrypted PEM private key, exactly as
`cat cert.cer key.pem` produces. Both in one value on purpose: the token request has to send
a thumbprint identifying the certificate AND a signature made by the matching key, and
splitting them across two secrets is an invitation to rotate one and not the other, which
fails at send time with an opaque Entra error. We derive the thumbprint from the certificate
instead, so it cannot drift. Entra never sees the private key - only the public half, uploaded
to the app registration by hand.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import roster
from .log import log, log_err, log_ok

# A single message: (recipient, subject, body).
Message = tuple[str, str, str]

_AUTHORITY = "https://login.microsoftonline.com"
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"
_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
# The assertion is single-use and consumed immediately; a short life caps the replay window.
_ASSERTION_TTL = 300
# A certificate gives no expiry warning of its own, and the failure mode is silent: enrolment
# codes and grade notices simply stop going out. Warn while there is still time to rotate.
_EXPIRY_WARN_DAYS = 30


# --------------------------------------------------------------------------- Microsoft Graph


@dataclass
class GraphConfig:
    tenant_id: str
    client_id: str
    cert_pem: str
    sender: str


# The env names the send workflows must carry. `workflows_render._MAIL_ENV` is asserted
# against this tuple, so a rename cannot reach production as a silently unconfigured org.
GRAPH_ENV = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_CERT", "GRAPH_SENDER")
# Every one of these is interpolated into a URL or a header. The certificate is NOT here:
# a PEM is multi-line by construction.
_SINGLE_LINE = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_SENDER")


def graph_config_from_env() -> GraphConfig | None:
    """Build the Graph config from env, or None if it is not fully configured.

    Silent when NOTHING is set - an offline `dry_run` preview is a documented feature. Loud
    when SOME of it is set, naming the variables at fault: Actions masks secret values, so
    a blanket "no transport configured" is undebuggable, and a half-configured transport is
    a misconfiguration rather than an absence.

    Whitespace inside a single-line value is refused BY NAME, never by value. `tenant_id`
    goes straight into the token URL, so a trailing newline - what `gh secret set < file`
    produces - raised `http.client.InvalidURL` out of the mail step, uncaught, into a public
    log, after the enrolment codes had already been committed."""
    found = {k: (os.environ.get(k) or "").strip() for k in GRAPH_ENV}
    missing = [k for k, v in found.items() if not v]
    if len(missing) == len(GRAPH_ENV):
        return None
    if missing:
        log_err(f"Graph is half-configured - unset or blank: {', '.join(missing)}")
        return None
    ragged = [k for k in _SINGLE_LINE if any(c.isspace() for c in found[k])]
    if ragged:
        log_err(f"whitespace inside {', '.join(ragged)} - re-set without line breaks")
        return None
    return GraphConfig(*(found[k] for k in GRAPH_ENV))


def _b64url(raw: bytes) -> str:
    """Unpadded base64url - what JWS uses for every segment."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_cert_and_key(
    cert_pem: str,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Parse the one-secret PEM into (certificate, private key).

    Raises RuntimeError, not the library's own errors: this runs in an Actions log where a
    malformed secret should read as one actionable line, not a cryptography traceback."""
    raw = cert_pem.encode()
    try:
        cert = x509.load_pem_x509_certificate(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"GRAPH_CLIENT_CERT has no readable PEM certificate ({exc}). It must hold the "
            f"certificate AND its private key, as `cat cert.cer key.pem` produces."
        ) from exc
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError) as exc:
        # TypeError is what cryptography raises for an ENCRYPTED key given no password - a
        # passphrase can never work here, since no one can type one into a runner.
        raise RuntimeError(
            f"GRAPH_CLIENT_CERT has no usable PEM private key ({exc}). The key must be "
            f"present in the same secret and must not be passphrase-protected."
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        # RuntimeError, not the TypeError ruff would prefer: every other bad-secret path here
        # raises RuntimeError precisely because `main()` catches it and logs one actionable
        # line. A TypeError would escape as a traceback in the Actions log.
        raise RuntimeError(  # noqa: TRY004
            f"GRAPH_CLIENT_CERT holds a {type(key).__name__} private key; Entra app "
            f"certificate credentials must be RSA."
        )
    return cert, key


def _warn_if_expiring(cert: x509.Certificate) -> None:
    """Log the certificate's remaining life when it is nearly up (or already gone)."""
    days = (cert.not_valid_after_utc - datetime.now(tz=UTC)).days
    if days < 0:
        log_err(
            f"the Graph certificate EXPIRED {-days} day(s) ago "
            f"({cert.not_valid_after_utc:%Y-%m-%d}) - sending will fail until it is replaced"
        )
    elif days <= _EXPIRY_WARN_DAYS:
        log_err(
            f"the Graph certificate expires in {days} day(s) "
            f"({cert.not_valid_after_utc:%Y-%m-%d}) - rotate it now"
        )


def _client_assertion(cfg: GraphConfig) -> str:
    """A JWT signed by the app's private key, standing in for a client secret.

    Entra looks the public half up by the `x5t` thumbprint in the header, then checks the
    signature against it. `x5t` is SHA-1 because the protocol says so - it is a certificate
    IDENTIFIER, not a security control; the signature itself is RS256 (SHA-256)."""
    cert, key = _load_cert_and_key(cfg.cert_pem)
    _warn_if_expiring(cert)
    now = int(time.time())
    header = {
        "alg": "RS256",
        "typ": "JWT",
        "x5t": _b64url(
            cert.fingerprint(hashes.SHA1())
        ),  # identifier only, see docstring
    }
    claims = {
        "aud": f"{_AUTHORITY}/{cfg.tenant_id}/oauth2/v2.0/token",
        "iss": cfg.client_id,
        "sub": cfg.client_id,
        "jti": str(uuid.uuid4()),
        "nbf": now,
        "exp": now + _ASSERTION_TTL,
    }
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode())
        for part in (header, claims)
    )
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


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
# Graph allows ~30 messages/minute per mailbox. 2.5s leaves headroom: a 126-row cohort takes
# ~5min, where the same batch sent back-to-back starts 429-ing around message 30 and then
# pays up to _MAX_SEND_ATTEMPTS x _RETRY_AFTER_CAP per recipient, serially.
_SEND_INTERVAL = 2.5
# Stop with time left in the 30-minute job. A batch KILLED at the job timeout takes the
# caller's sent-marker write down with it, and the re-run then re-mails everyone who already
# got one; a batch that stops itself reports what went out and is resumable.
_BATCH_BUDGET = 20 * 60


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
    except (http.client.HTTPException, ValueError) as exc:
        # A malformed URL or a half-closed connection is a transport failure, not a bug to
        # traceback out of a public Actions log. URLError is an OSError and is caught above;
        # InvalidURL, RemoteDisconnected and BadStatusLine are not.
        return 0, str(exc).encode(), {}


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
            "client_assertion_type": _ASSERTION_TYPE,
            "client_assertion": _client_assertion(cfg),
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
        # The last attempt cannot `continue` (the guard above requires another to come),
        # so every path out of this loop is one of the two returns.
        log_err(f"send to {mask_email(to)} failed ({status})")
        return False


def _send_via_graph(cfg: GraphConfig, messages: list[Message]) -> list[str]:
    """Send the whole batch on one token. Returns the recipients that actually went out.

    Addresses, not a count: the caller records who was mailed so a re-run does not mail
    them again, and a marker written off a count re-mails exactly the recipients that
    succeeded.

    A failed token request raises rather than returning empty: nothing was sent AND nothing
    could be, which is a transport failure, not an empty batch - the two must not read
    the same to the caller."""
    token = _graph_token(cfg)
    if token is None:
        raise RuntimeError(
            "Microsoft Graph token request failed - check the GRAPH_* secrets. Nothing sent."
        )
    sent: list[str] = []
    started = time.monotonic()
    for index, (to, subject, body) in enumerate(messages):
        if index:
            if time.monotonic() - started > _BATCH_BUDGET:
                log_err(
                    f"stopped after {len(sent)} of {len(messages)} message(s) "
                    f"- re-run to continue"
                )
                break
            time.sleep(_SEND_INTERVAL)
        if _graph_send_one(cfg, token, to, subject, body):
            log_ok(f"sent -> {mask_email(to)}")
            sent.append(to)
    return sent


# ---------------------------------------------------------------------------------- public


SAMPLE_HEADER = "--- sample (placeholders) ---"


def preflight() -> None:
    """Prove the credential without sending anything. Raises if it is set but broken.

    A preview otherwise validates nothing about the secrets: it returns before the transport
    is chosen, so it reads the same whether the certificate is right, wrong or absent - and
    the first real run is the first test, against real students."""
    cfg = graph_config_from_env()
    if cfg is None:
        log_err(
            "no mail transport configured - this preview proves nothing about a send"
        )
        return
    if _graph_token(cfg) is None:
        raise RuntimeError(
            "Microsoft Graph token request failed - check the GRAPH_* secrets. Nothing sent."
        )
    log_ok(f"Graph credential OK - would send as {cfg.sender}")


def send_bulk(
    messages: list[Message], dry_run: bool = False, sample: str | None = None
) -> list[str]:
    """Preview (dry_run) or send a batch. Returns the recipients previewed/sent.

    dry_run lists masked recipients + subjects and sends nothing. Never a REAL body: the
    enrolment-code email carries the student's name and a live credential, and this runs
    in a public repo whose Actions log anyone can read.

    `sample` is the one thing a masked list cannot give a reviewer - the wording. It is a
    body the CALLER rendered from placeholders (`<name>`, `<code>`), never one of
    `messages`, and it is printed once, under `SAMPLE_HEADER`, so faculty can proof-read
    the email before a real send."""
    if dry_run:
        for to, subject, _body in messages:
            log(f"  would send -> {mask_email(to)}: {subject}")
        if sample:
            log(SAMPLE_HEADER)
            log(sample)
        log_ok(f"DRY-RUN previewed {len(messages)} message(s) - nothing sent")
        preflight()
        return [to for to, _subject, _body in messages]

    graph = graph_config_from_env()
    if graph is None:
        log_err("No mail transport configured - set the GRAPH_* secrets. Nothing sent.")
        return []
    return _send_via_graph(graph, messages)


def sample_of(
    build: Callable[[roster.Student], tuple[str, str, str]], **placeholders: str
) -> str:
    """One message rendered against a PLACEHOLDER student, for a dry-run preview.

    A dry run masks every recipient and prints no real body, which leaves the one thing a
    reviewer actually wants to check - the wording - invisible. `build` is the caller's
    own (to, subject, body) builder; `placeholders` override the stand-in student's fields
    where the message shows one (`<code>`, `<handle>`). No real name, handle or credential
    appears, so the result is safe in a world-readable Actions log."""
    fields = {
        "hertie_email": "<email>",
        "name": "<name>",
        "github_handle": "",
        "github_id": "",
    } | placeholders
    return build(roster.Student(**fields))[2]
