"""E-mail, only for the «¿Olvidaste la contraseña?» links (and the notice that the recovery address changed).

Plain SMTP, so any provider works (a transactional service with the server's own domain is the sensible choice; see
docs/deployment.md). Without `SMTP_HOST` and `SMTP_FROM`, or without an address to build the links on (`PUBLIC_URL` or
`WEBAUTHN_ORIGIN`), EVBro does not offer the option at all.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.config import settings

_LOGGER = logging.getLogger("app.services.mailer")
TIMEOUT_S = 20
EMAIL_MAX_LENGTH = 254
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")


def normalize_email(raw: str | None) -> str | None:
    """The address as it is kept (trimmed, lower case), or None when it is empty. Raises ValueError when it is not an address."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if len(text) > EMAIL_MAX_LENGTH or not _EMAIL_RE.match(text):
        raise ValueError("bad_email")
    return text


def public_url() -> str | None:
    """Where people open EVBro, for the links. Never taken from the request (a forged Host header would send the link elsewhere)."""
    for candidate in (settings.PUBLIC_URL, *(settings.WEBAUTHN_ORIGIN or "").split(",")):
        url = (candidate or "").strip().rstrip("/")
        if url.startswith(("https://", "http://")):
            return url
    return None


def enabled() -> bool:
    """Can EVBro send a password-reset link? SMTP configured and an address to build the link on."""
    return bool(settings.SMTP_HOST and settings.SMTP_FROM and public_url())


def send(to: str, subject: str, body: str) -> bool:
    """Send a plain-text e-mail. BLOCKING. Returns False (and logs why, without the address) when it could not be sent."""
    if not (settings.SMTP_HOST and settings.SMTP_FROM):
        return False
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid(domain=(settings.SMTP_FROM.rsplit("@", 1)[-1].strip(" >") or None))
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    security = (settings.SMTP_SECURITY or "starttls").strip().lower()
    try:
        context = ssl.create_default_context()
        if security == "ssl":
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=TIMEOUT_S, context=context)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=TIMEOUT_S)
        with server:
            if security == "starttls":
                server.starttls(context=context)
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as e:
        _LOGGER.error("Could not send an e-mail through %s: %s", settings.SMTP_HOST, type(e).__name__)
        return False
