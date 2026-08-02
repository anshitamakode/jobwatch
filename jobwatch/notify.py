"""Where alerts go. Telegram is the default: instant push, free, no SMTP pain."""

from __future__ import annotations

import html
import os
import smtplib
import textwrap
from email.message import EmailMessage

import requests

from .matcher import Match

TG_API = "https://api.telegram.org"
MAX_TG_CHARS = 3800


def _fmt_one(m: Match, idx: int) -> str:
    p = m.posting
    tag = "🧠" if "ai" in m.profile.lower() or "ml" in m.profile.lower() else "☕"
    reasons = ", ".join(m.reasons[:5]) or "title match"
    return (
        f"{tag} <b>{html.escape(p.title)}</b>\n"
        f"{html.escape(p.company)} · {html.escape(p.location or 'location n/a')}\n"
        f"Resume: <b>{html.escape(m.resume)}</b> · score {m.score}\n"
        f"<i>{html.escape(reasons)}</i>\n"
        f'<a href="{html.escape(p.url, quote=True)}">Apply →</a>'
    )


def _chunks(blocks: list[str], limit: int = MAX_TG_CHARS):
    buf, size = [], 0
    for b in blocks:
        if size + len(b) > limit and buf:
            yield "\n\n".join(buf)
            buf, size = [], 0
        buf.append(b)
        size += len(b) + 2
    if buf:
        yield "\n\n".join(buf)


def send_telegram(matches: list[Match], token: str, chat_id: str) -> None:
    matches = sorted(matches, key=lambda m: -m.score)
    header = f"<b>{len(matches)} new posting{'s' if len(matches) != 1 else ''}</b>"
    blocks = [header] + [_fmt_one(m, i) for i, m in enumerate(matches)]
    for chunk in _chunks(blocks):
        r = requests.post(
            f"{TG_API}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text[:300]}")


def send_email(matches: list[Match], cfg: dict) -> None:
    matches = sorted(matches, key=lambda m: -m.score)
    lines = []
    for m in matches:
        p = m.posting
        lines.append(
            f"{p.title}\n  {p.company} | {p.location}\n"
            f"  resume: {m.resume} (score {m.score})\n  {p.url}\n"
        )
    msg = EmailMessage()
    msg["Subject"] = f"[jobwatch] {len(matches)} new posting(s)"
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content("\n".join(lines))
    with smtplib.SMTP(cfg["host"], cfg.get("port", 587)) as s:
        s.starttls()
        s.login(cfg["from"], os.environ[cfg["password_env"]])
        s.send_message(msg)


def print_console(matches: list[Match]) -> None:
    for m in sorted(matches, key=lambda x: -x.score):
        p = m.posting
        print(f"\n[{m.score:>3}] {p.title}")
        print(f"      {p.company} · {p.location}")
        print(f"      resume: {m.resume}  ({', '.join(m.reasons[:5])})")
        print(f"      {p.url}")


def dispatch(matches: list[Match], config: dict, quiet: bool = False) -> None:
    if not matches:
        return
    n = config.get("notify", {})
    sent = False

    tg = n.get("telegram", {})
    tok = os.environ.get(tg.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if tg.get("enabled") and tok and chat:
        send_telegram(matches, tok, chat)
        sent = True

    em = n.get("email", {})
    if em.get("enabled"):
        send_email(matches, em)
        sent = True

    if not quiet and (not sent or n.get("console", True)):
        print_console(matches)
