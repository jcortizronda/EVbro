"""«¿Olvidaste la contraseña?»: a single-use link by e-mail to the address the account itself set (services/mailer.py).

The public routes always answer the same, whether the account exists or not, has an address or not: whoever asks learns nothing
about which accounts exist. The link carries the token in the URL fragment (`/#reset=…`), which browsers never send to any server
nor in the Referer. Only its hash is kept; it lasts RESET_TTL and is good for one use; a new request voids the earlier links.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import secrets

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppUser, PasswordReset
from app.i18n import _, reset_language, set_language
from app.services import email_templates, mailer
from app.timeutil import utcnow

_LOGGER = logging.getLogger("app.services.password_reset")
RESET_TTL = datetime.timedelta(minutes=30)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def accounts_for(db: AsyncSession, login: str) -> list[AppUser]:
    """The accounts a «usuario o email» names: by username, or by the e-mail they set. Never the administrators (their console
    has its own way back), and only accounts with an address."""
    text = (login or "").strip().lower()
    if not text:
        return []
    cond = AppUser.username == text
    if "@" in text:
        cond = or_(cond, AppUser.email == text)
    rows = (await db.execute(select(AppUser).where(cond, AppUser.is_admin.is_(False), AppUser.email.is_not(None)))).scalars().all()
    return list(rows)


def _frame(user: AppUser) -> dict:
    """What every message shares: the greeting with the name apart (bold in the HTML), the footer and the tagline."""
    return {"lang": user.language or "es", "greeting": _("Hola, {p0}:", p0="{name}"), "greeting_name": user.username,
            "footer": _("Este es un mensaje automático de EVbro; no respondas a este correo."), "tagline": _("El compañero de tu coche")}


def compose(user: AppUser, link: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML of the reset e-mail, in the account's language."""
    token = set_language(user.language)
    try:
        minutes = int(RESET_TTL.total_seconds() // 60)
        subject = _("EVbro: restablecer la contraseña")
        asked = _("Alguien (seguramente tú) ha pedido restablecer la contraseña de tu cuenta de EVbro.")
        ignore = _("Si no lo has pedido tú, ignora este correo: tu contraseña no cambia.")
        text = "\n\n".join([
            _("Hola, {p0}:", p0=user.username),
            asked,
            _("Para elegir una nueva, abre este enlace. Caduca en {p0} minutos y solo vale una vez:", p0=minutes) + "\n" + link,
            ignore,
        ]) + "\n"
        html = email_templates.render(email_templates.Email(
            **_frame(user), preheader=_("El enlace caduca en {p0} minutos.", p0=minutes), title=_("Restablece tu contraseña"),
            paragraphs=[asked, _("Pulsa el botón para elegir una contraseña nueva.")],
            button_label=_("Elegir contraseña nueva"), button_url=link,
            after_button=_("El enlace caduca en {p0} minutos y solo vale una vez.", p0=minutes),
            fallback_label=_("Si el botón no funciona, copia esta dirección en el navegador:"), notice=ignore), mailer.public_url() or "")
        return subject, text, html
    finally:
        reset_language(token)


def compose_email_changed(user: AppUser, new_email: str | None) -> tuple[str, str, str]:
    """The notice to the address that stops being the account's (subject, plain text, HTML), in the account's language."""
    token = set_language(user.language)
    try:
        subject = _("EVbro: el email de tu cuenta ha cambiado")
        change = (_("El email de recuperación de tu cuenta de EVbro ({p0}) ahora es otro.", p0=user.username) if new_email
                  else _("Se ha quitado el email de recuperación de tu cuenta de EVbro ({p0}).", p0=user.username))
        warning = _("Si no lo has hecho tú, entra en EVbro, cambia la contraseña y vuelve a poner tu email.")
        text = "\n\n".join([change, warning]) + "\n"
        base = mailer.public_url() or ""
        html = email_templates.render(email_templates.Email(
            **_frame(user), preheader=change, title=_("El email de tu cuenta ha cambiado"), paragraphs=[change],
            button_label=_("Abrir EVbro") if base else None, button_url=base or None, notice=warning), base)
        return subject, text, html
    finally:
        reset_language(token)


async def issue(db: AsyncSession, user: AppUser, ip: str | None) -> str:
    """A new link for this account (the unused earlier ones stop working). Returns the URL to send."""
    now = utcnow()
    await db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
                     .values(used_at=now))
    token = secrets.token_urlsafe(32)
    db.add(PasswordReset(user_id=user.id, token_hash=hash_token(token), created_at=now, expires_at=now + RESET_TTL, ip=(ip or "")[:64]))
    await db.commit()
    return f"{mailer.public_url()}/#reset={token}"


async def find(db: AsyncSession, token: str) -> tuple[PasswordReset, AppUser] | None:
    """The still valid link and its account; None when it is unknown, used or expired. The caller marks it used (`used_at`)
    once the new password is accepted, so a password the rules refuse does not burn the link."""
    if not token or len(token) > 200:
        return None
    row = (await db.execute(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)))).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return None
    user = await db.get(AppUser, row.user_id)
    return (row, user) if user is not None else None
