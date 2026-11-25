"""The HTML of EVbro's e-mails: one layout (logo, title, text, a button, a notice and the footer) that mail clients render alike.

Mail clients are old browsers: tables for the layout, every style inline, no web fonts, no SVG, no scripts. The logo is the app's
PNG icon served by this server (`PUBLIC_URL`). Whatever comes from a user (the username) is escaped here. The plain-text part
is still sent next to it (services/mailer.py) for the clients that do not show HTML.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

ACCENT = "#0071e3"            # the app's --accent-bright
INK = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
PAGE = "#f4f5f7"
NOTICE_BG = "#f5f8ff"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


@dataclass
class Email:
    """What a message says. `paragraphs` and `notice` are plain text (escaped here); `greeting_name` is shown in bold."""
    lang: str
    preheader: str
    title: str
    greeting: str                     # e.g. "Hola, {name}:" with `{name}` where the bold name goes
    greeting_name: str
    paragraphs: list[str]
    code: str | None = None           # a code to show apart (e.g. an invitation's), in a box
    code_label: str | None = None
    button_label: str | None = None
    button_url: str | None = None
    after_button: str | None = None   # e.g. how long the link lasts
    fallback_label: str | None = None  # «if the button does not work, copy this address»
    links: list[tuple[str, str]] | None = None   # (label, url) shown as plain links after the button, e.g. a manual
    closing: list[str] | None = None  # paragraphs after the button and the links
    notice: str | None = None         # the security line, in a soft box
    footer: str = ""
    tagline: str = ""


def _p(text: str, *, size: int = 16, color: str = INK, margin: str = "0 0 16px") -> str:
    return (f'<p style="margin:{margin};font-family:{FONT};font-size:{size}px;line-height:1.55;color:{color};">'
            f"{escape(text)}</p>")


def render(mail: Email, public_url: str) -> str:
    host = urlsplit(public_url).netloc or public_url
    logo = f"{public_url}/static/icons/icon-192.png"
    name = f'<strong style="color:{INK};">{escape(mail.greeting_name)}</strong>'
    greeting = escape(mail.greeting).replace(escape("{name}"), name)
    body = [f'<p style="margin:0 0 16px;font-family:{FONT};font-size:16px;line-height:1.55;color:{INK};">{greeting}</p>']
    body += [_p(p) for p in mail.paragraphs]
    if mail.code:
        label = (f'<div style="font-family:{FONT};font-size:12px;letter-spacing:0.4px;text-transform:uppercase;color:{MUTED};'
                 f'margin:0 0 6px;">{escape(mail.code_label)}</div>' if mail.code_label else "")
        body.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:8px 0 4px;"><tr>'
            f'<td style="background:{PAGE};border:1px solid {LINE};border-radius:12px;padding:14px 16px;">{label}'
            f'<div style="font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:15px;color:{INK};word-break:break-all;">'
            f"{escape(mail.code)}</div></td></tr></table>")
    if mail.button_label and mail.button_url:
        url = escape(mail.button_url, quote=True)
        body.append(
            '<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:28px 0 8px;"><tr>'
            f'<td align="center" bgcolor="{ACCENT}" style="border-radius:12px;">'
            f'<a href="{url}" target="_blank" style="display:inline-block;padding:14px 28px;font-family:{FONT};font-size:16px;'
            f'font-weight:600;color:#ffffff;text-decoration:none;border-radius:12px;">{escape(mail.button_label)}</a>'
            "</td></tr></table>")
        if mail.after_button:
            body.append(_p(mail.after_button, size=14, color=MUTED, margin="0 0 24px"))
        if mail.fallback_label:
            body.append(_p(mail.fallback_label, size=13, color=MUTED, margin="0 0 6px"))
            body.append(f'<p style="margin:0 0 24px;font-family:{FONT};font-size:13px;line-height:1.5;word-break:break-all;">'
                        f'<a href="{url}" target="_blank" style="color:{ACCENT};text-decoration:underline;">{escape(mail.button_url)}</a></p>')
    for label, href in mail.links or []:
        body.append(f'<p style="margin:0 0 12px;font-family:{FONT};font-size:15px;line-height:1.5;">'
                    f'<a href="{escape(href, quote=True)}" target="_blank" style="color:{ACCENT};font-weight:600;text-decoration:none;">'
                    f"{escape(label)} &rarr;</a></p>")
    body += [_p(p) for p in mail.closing or []]
    if mail.notice:
        body.append(
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:8px 0 0;"><tr>'
            f'<td style="background:{NOTICE_BG};border-left:4px solid {ACCENT};border-radius:8px;padding:14px 16px;'
            f'font-family:{FONT};font-size:14px;line-height:1.5;color:{INK};">{escape(mail.notice)}</td></tr></table>')

    return f"""<!DOCTYPE html>
<html lang="{escape(mail.lang)}" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{escape(mail.title)}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{escape(mail.preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:{PAGE};">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;">
    <tr><td style="padding:0 4px 20px;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>
        <td style="vertical-align:middle;"><img src="{escape(logo, quote=True)}" width="40" height="40" alt="EVbro"
          style="display:block;border:0;border-radius:10px;"></td>
        <td style="vertical-align:middle;padding-left:12px;font-family:{FONT};">
          <div style="font-size:20px;font-weight:700;color:{INK};letter-spacing:-0.2px;">EVbro</div>
          <div style="font-size:13px;color:{MUTED};">{escape(mail.tagline)}</div>
        </td>
      </tr></table>
    </td></tr>
    <tr><td style="background:#ffffff;border:1px solid {LINE};border-radius:16px;padding:36px 32px;">
      <h1 style="margin:0 0 20px;font-family:{FONT};font-size:24px;line-height:1.3;font-weight:700;color:{INK};">{escape(mail.title)}</h1>
      {"".join(body)}
    </td></tr>
    <tr><td style="padding:20px 8px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};text-align:center;">
      {escape(mail.footer)}<br>
      <a href="{escape(public_url, quote=True)}" target="_blank" style="color:{MUTED};text-decoration:underline;">{escape(host)}</a>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""
