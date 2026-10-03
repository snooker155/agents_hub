"""
The public consent page: plain HTML rendered by the backend, no script, no
login, in the end user's language (English, Russian or German, picked from
``Accept-Language``).

It shows what one request asks for and nothing else: the workspace, the
agent, the provider, the access in plain words from the fixed catalog
(connectors/consent/catalog.py) and the agent's own one-line purpose, quoted
as the agent's words. Everything is escaped; the purpose is the only text a
model wrote and it is labelled as such.

Colors come from the dashboard's generated theme (dashboard/frontend/src/theme.css):
its ``:root`` tokens are inlined, and its dark tokens apply under
``prefers-color-scheme: dark``. Where the file is not shipped, the page falls
back to the browser's system colors rather than inventing a palette.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import catalog

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "page_title": "Account access",
        "title": "Give {agent} access to your {provider} account",
        "intro": "{agent} in {workspace} asks to act on your {provider} account for you.",
        "purpose": "What the agent says it needs it for:",
        "access": "It will be able to:",
        "safety": "Only continue if you asked for this in your conversation. You can take the "
                  "access back at any time: tell the agent to disconnect, or remove the app in "
                  "your {provider} account settings.",
        "expires": "This link works once and expires at {time} UTC.",
        "continue": "Continue with {provider}",
        "decline": "No, thanks",
        "done_title": "Access given",
        "done_body": "{agent} can now use your {provider} account{email}. You can close this tab "
                     "and go back to the conversation.",
        "denied_title": "No access given",
        "denied_body": "Nothing was shared. You can close this tab.",
        "expired_title": "This link has expired",
        "expired_body": "Ask the agent for a new one in your conversation.",
        "invalid_title": "This link is not valid",
        "invalid_body": "It may be mistyped or already used. Ask the agent for a new one.",
        "used_title": "This link was already used",
        "used_body": "Ask the agent for a new one if you still want to give access.",
        "granted_title": "Access was already given",
        "granted_body": "Nothing more to do here. You can close this tab.",
        "revoked_title": "This access was removed",
        "revoked_body": "Ask the agent for a new link if you want to give access again.",
        "failed_title": "Something went wrong",
        "failed_body": "The access could not be set up and nothing was stored. Ask the agent for "
                       "a new link and try again.",
        "busy_title": "Too many requests",
        "busy_body": "Wait a minute and try again.",
    },
    "ru": {
        "page_title": "Доступ к аккаунту",
        "title": "Дать {agent} доступ к вашему аккаунту {provider}",
        "intro": "{agent} в пространстве {workspace} просит действовать от вашего имени в аккаунте {provider}.",
        "purpose": "Зачем это нужно, по словам агента:",
        "access": "Агент сможет:",
        "safety": "Продолжайте, только если вы сами просили об этом в разговоре. Доступ можно "
                  "забрать в любой момент: попросите агента отключиться или удалите приложение в "
                  "настройках аккаунта {provider}.",
        "expires": "Ссылка одноразовая и действует до {time} UTC.",
        "continue": "Продолжить с {provider}",
        "decline": "Нет, спасибо",
        "done_title": "Доступ выдан",
        "done_body": "Теперь {agent} может пользоваться вашим аккаунтом {provider}{email}. Эту "
                     "вкладку можно закрыть и вернуться к разговору.",
        "denied_title": "Доступ не выдан",
        "denied_body": "Ничего не передано. Вкладку можно закрыть.",
        "expired_title": "Срок ссылки истёк",
        "expired_body": "Попросите агента прислать новую в разговоре.",
        "invalid_title": "Ссылка недействительна",
        "invalid_body": "Возможно, в ней опечатка или она уже использована. Попросите агента прислать новую.",
        "used_title": "Ссылка уже использована",
        "used_body": "Попросите агента прислать новую, если всё ещё хотите дать доступ.",
        "granted_title": "Доступ уже выдан",
        "granted_body": "Больше ничего делать не нужно. Вкладку можно закрыть.",
        "revoked_title": "Этот доступ отозван",
        "revoked_body": "Попросите агента прислать новую ссылку, если хотите снова дать доступ.",
        "failed_title": "Что-то пошло не так",
        "failed_body": "Доступ настроить не удалось, ничего не сохранено. Попросите агента "
                       "прислать новую ссылку и попробуйте снова.",
        "busy_title": "Слишком много запросов",
        "busy_body": "Подождите минуту и попробуйте снова.",
    },
    "de": {
        "page_title": "Kontozugriff",
        "title": "{agent} Zugriff auf Ihr {provider} Konto geben",
        "intro": "{agent} im Arbeitsbereich {workspace} möchte in Ihrem {provider} Konto für Sie handeln.",
        "purpose": "Wofür der Agent ihn nach eigener Aussage braucht:",
        "access": "Der Agent kann dann:",
        "safety": "Fahren Sie nur fort, wenn Sie im Gespräch selbst darum gebeten haben. Sie können "
                  "den Zugriff jederzeit zurücknehmen: Bitten Sie den Agenten, die Verbindung zu "
                  "trennen, oder entfernen Sie die App in den Einstellungen Ihres {provider} Kontos.",
        "expires": "Dieser Link gilt einmal und läuft um {time} UTC ab.",
        "continue": "Weiter mit {provider}",
        "decline": "Nein, danke",
        "done_title": "Zugriff erteilt",
        "done_body": "{agent} kann jetzt Ihr {provider} Konto nutzen{email}. Sie können diesen Tab "
                     "schließen und zum Gespräch zurückkehren.",
        "denied_title": "Kein Zugriff erteilt",
        "denied_body": "Es wurde nichts geteilt. Sie können diesen Tab schließen.",
        "expired_title": "Dieser Link ist abgelaufen",
        "expired_body": "Bitten Sie den Agenten im Gespräch um einen neuen.",
        "invalid_title": "Dieser Link ist ungültig",
        "invalid_body": "Er ist vielleicht falsch kopiert oder schon benutzt. Bitten Sie den Agenten um einen neuen.",
        "used_title": "Dieser Link wurde schon benutzt",
        "used_body": "Bitten Sie den Agenten um einen neuen, wenn Sie weiterhin Zugriff geben möchten.",
        "granted_title": "Zugriff wurde bereits erteilt",
        "granted_body": "Hier ist nichts mehr zu tun. Sie können diesen Tab schließen.",
        "revoked_title": "Dieser Zugriff wurde entfernt",
        "revoked_body": "Bitten Sie den Agenten um einen neuen Link, wenn Sie wieder Zugriff geben möchten.",
        "failed_title": "Etwas ist schiefgelaufen",
        "failed_body": "Der Zugriff konnte nicht eingerichtet werden, es wurde nichts gespeichert. "
                       "Bitten Sie den Agenten um einen neuen Link und versuchen Sie es erneut.",
        "busy_title": "Zu viele Anfragen",
        "busy_body": "Warten Sie eine Minute und versuchen Sie es erneut.",
    },
}

#: The end states a page can show, each with a title and a body string.
OUTCOMES = ("done", "denied", "expired", "invalid", "used", "granted", "revoked", "failed", "busy")


def pick_language(accept_language: Optional[str]) -> str:
    """The best of en, ru, de for an ``Accept-Language`` header; en by default."""
    best, best_q = "en", -1.0
    for index, part in enumerate(str(accept_language or "").split(",")):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        q = 1.0
        match = re.search(r"q\s*=\s*([0-9.]+)", params)
        if match:
            try:
                q = float(match.group(1))
            except ValueError:
                q = 0.0
        lang = tag.strip().lower().split("-")[0]
        # Earlier entries win a tie, as the header orders them.
        if lang in catalog.LANGUAGES and (q > best_q):
            best, best_q = lang, q
    return best


def _t(lang: str, key: str, **values: Any) -> str:
    text = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"][key]
    return text.format(**{k: v for k, v in values.items()})


@lru_cache(maxsize=1)
def theme_css() -> str:
    """The dashboard's color tokens: light under ``:root``, dark under the
    media query. Empty when the theme file is not shipped."""
    path = Path(__file__).resolve().parents[2] / "dashboard" / "frontend" / "src" / "theme.css"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    light = re.search(r"(?m)^:root\s*\{(.*?)^\}", text, re.S)
    dark = re.search(r"(?m)^html\.dark\s*\{(.*?)^\}", text, re.S)
    out = ""
    if light:
        out += ":root {" + light.group(1) + "}\n"
    if dark:
        out += "@media (prefers-color-scheme: dark) { :root {" + dark.group(1) + "} }\n"
    return out


_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--surface-page, Canvas); color: var(--text-primary, CanvasText); }
main { max-width: 560px; margin: 0 auto; padding: 48px 16px; }
.card { background: var(--surface-card, Canvas); border: 1px solid var(--border-default, GrayText);
  border-radius: 12px; padding: 28px; }
h1 { font-size: 20px; line-height: 1.3; margin: 0 0 12px; }
p { margin: 0 0 12px; color: var(--text-secondary, CanvasText); }
.muted { color: var(--text-muted, GrayText); font-size: 13px; }
blockquote { margin: 0 0 16px; padding: 10px 14px; border-left: 3px solid var(--brand, Highlight);
  background: var(--surface-sunken, Canvas); color: var(--text-primary, CanvasText); }
ul { margin: 0 0 16px; padding-left: 20px; }
li { margin: 4px 0; }
.note { background: var(--warn-surface, Canvas); color: var(--text-primary, CanvasText);
  border-radius: 8px; padding: 10px 14px; font-size: 13px; margin: 0 0 16px; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; }
button { font: inherit; border-radius: 8px; padding: 10px 18px; cursor: pointer;
  border: 1px solid var(--border-strong, ButtonBorder); background: var(--surface-raised, ButtonFace);
  color: var(--text-primary, ButtonText); }
button.primary { background: var(--brand, Highlight); color: var(--brand-contrast, HighlightText);
  border-color: var(--brand, Highlight); }
"""


def _document(lang: str, body: str) -> str:
    title = html.escape(_t(lang, "page_title"))
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex">'
            f"<title>{title}</title><style>{theme_css()}{_STYLE}</style></head>"
            f'<body><main><div class="card">{body}</div></main></body></html>')


def render_request(row: Dict[str, Any], *, lang: str, agent_name: str, token: str) -> str:
    """The page for an open request: what is asked, Continue and No."""
    e = html.escape
    provider = catalog.PROVIDER_LABELS.get(row["provider"], row["provider"])
    agent = e(agent_name or row["agent_id"])
    workspace = e(row["workspace"])
    items: List[str] = [f"<li>{e(catalog.label(k, lang))}</li>" for k in row.get("scopes") or []]
    purpose = str(row.get("purpose") or "").strip()
    expires = str(row.get("expires_at") or "")[:16].replace("T", " ")
    action = f"/consent/{e(token)}"
    parts = [
        f"<h1>{_t(lang, 'title', agent=agent, provider=e(provider))}</h1>",
        f"<p>{_t(lang, 'intro', agent=agent, workspace=workspace, provider=e(provider))}</p>",
    ]
    if purpose:
        parts.append(f'<p class="muted">{e(_t(lang, "purpose"))}</p><blockquote>{e(purpose)}</blockquote>')
    parts.append(f"<p>{e(_t(lang, 'access'))}</p><ul>{''.join(items)}</ul>")
    parts.append(f'<p class="note">{e(_t(lang, "safety", provider=provider))}</p>')
    parts.append(
        '<div class="actions">'
        f'<form method="post" action="{action}/start"><button class="primary" type="submit">'
        f'{e(_t(lang, "continue", provider=provider))}</button></form>'
        f'<form method="post" action="{action}/decline"><button type="submit">'
        f'{e(_t(lang, "decline"))}</button></form></div>')
    parts.append(f'<p class="muted" style="margin-top:16px">{e(_t(lang, "expires", time=expires))}</p>')
    return _document(lang, "".join(parts))


def render_outcome(outcome: str, *, lang: str, agent_name: str = "", provider: str = "",
                   account_email: str = "") -> str:
    """An end page: done, denied, expired, invalid and the rest of :data:`OUTCOMES`."""
    e = html.escape
    outcome = outcome if outcome in OUTCOMES else "invalid"
    label = catalog.PROVIDER_LABELS.get(provider, provider)
    email = f" ({e(account_email)})" if account_email else ""
    title = e(_t(lang, f"{outcome}_title"))
    body = _t(lang, f"{outcome}_body", agent=e(agent_name or ""), provider=e(label), email=email)
    return _document(lang, f"<h1>{title}</h1><p>{body}</p>")


__all__ = ["OUTCOMES", "STRINGS", "pick_language", "render_outcome", "render_request", "theme_css"]
