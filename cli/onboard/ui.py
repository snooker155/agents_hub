"""How `ah setup` asks its questions: at the terminal, or from an answers file.

Every question carries a key (``database``, ``providers.openai.api_key``), so
the steps are written once and run two ways. :class:`TerminalAsker` prompts;
:class:`AnswersAsker` looks the key up in a JSON document instead, falls back
to the question's default, and stops with a clear message when a required
answer is missing, which is what makes an unattended install scriptable:

    ah setup --answers setup.json --yes
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

# (value, label, hint)
Option = tuple[str, str, str]


class SetupError(Exception):
    """A question the wizard cannot get past: a bad or missing answer."""


class Asker:
    interactive = False

    def __init__(self, console: Console) -> None:
        self.console = console

    # ---- output, the same either way ----

    def section(self, step: int, total: int, title: str, hint: str = "") -> None:
        self.console.print()
        label = f"{step}/{total}  {title}" if total else title
        self.console.rule(f"[bold]{label}[/bold]", align="left", style="cyan")
        if hint:
            self.console.print(f"[dim]{hint}[/dim]")

    def note(self, text: str) -> None:
        self.console.print(f"  {text}")

    def ok(self, text: str) -> None:
        self.console.print(f"  [green]✓[/green] {text}")

    def warn(self, text: str) -> None:
        self.console.print(f"  [yellow]![/yellow] {text}")

    def panel(self, body: str, title: str = "", style: str = "cyan") -> None:
        self.console.print(Panel(body, title=title, border_style=style, expand=False))

    # ---- questions ----

    def choose(self, key: str, question: str, options: Sequence[Option], default: Optional[str] = None) -> str:
        raise NotImplementedError

    def multi(self, key: str, question: str, options: Sequence[Option], default: Sequence[str] = ()) -> list[str]:
        raise NotImplementedError

    def text(self, key: str, question: str, default: str = "", required: bool = False,
             validate: Optional[Callable[[str], Optional[str]]] = None) -> str:
        raise NotImplementedError

    def secret(self, key: str, question: str, current: str = "", confirm: bool = False,
               allow_empty: bool = True) -> str:
        """A hidden value. Empty keeps ``current`` (shown masked) when there is one."""
        raise NotImplementedError

    def answered(self, key: str) -> bool:
        """Whether an answer for ``key`` was given in advance (an answers file),
        so a question QuickStart leaves out is still applied when one was."""
        return False

    def confirm(self, key: str, question: str, default: bool = True) -> bool:
        raise NotImplementedError


class TerminalAsker(Asker):
    interactive = True

    def _render_options(self, options: Sequence[Option], marked: Sequence[str] = ()) -> None:
        width = max(len(label) for _, label, _ in options)
        for i, (value, label, hint) in enumerate(options, 1):
            mark = "[green]●[/green] " if value in marked else "  "
            tail = f"  [dim]{hint}[/dim]" if hint else ""
            self.console.print(f"  {mark}[bold]{i:>2}[/bold]  {label.ljust(width)}{tail}")

    def _pick(self, raw: str, options: Sequence[Option]) -> Optional[str]:
        raw = raw.strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for value, label, _ in options:
            if raw.lower() in (value.lower(), label.lower()):
                return value
        return None

    def choose(self, key, question, options, default=None):
        self.console.print(f"\n[bold]?[/bold] {question}")
        self._render_options(options)
        default_no = next((str(i) for i, o in enumerate(options, 1) if o[0] == default), None)
        while True:
            raw = Prompt.ask("  Choice", default=default_no or None, console=self.console) or ""
            picked = self._pick(raw, options)
            if picked is not None:
                return picked
            self.console.print(f"  [red]Pick a number from 1 to {len(options)}.[/red]")

    def multi(self, key, question, options, default=()):
        self.console.print(f"\n[bold]?[/bold] {question}")
        self.console.print("  [dim]Numbers separated by spaces or commas; ● is the current selection; "
                           "Enter keeps it, - selects none.[/dim]")
        self._render_options(options, marked=default)
        while True:
            raw = Prompt.ask("  Choice", default=" ".join(
                str(i) for i, o in enumerate(options, 1) if o[0] in default) or None, console=self.console) or ""
            if raw.strip() == "-":
                return []
            parts = [p for p in raw.replace(",", " ").split() if p]
            picked = [self._pick(p, options) for p in parts]
            if all(p is not None for p in picked):
                return list(dict.fromkeys(picked))  # type: ignore[arg-type]
            self.console.print(f"  [red]Use numbers from 1 to {len(options)}.[/red]")

    def text(self, key, question, default="", required=False, validate=None):
        while True:
            value = (Prompt.ask(f"[bold]?[/bold] {question}", default=default or None,
                                console=self.console) or "").strip()
            if required and not value:
                self.console.print("  [red]This one is required.[/red]")
                continue
            problem = validate(value) if validate and value else None
            if problem:
                self.console.print(f"  [red]{problem}[/red]")
                continue
            return value

    def secret(self, key, question, current="", confirm=False, allow_empty=True):
        from cli.onboard.probe import mask
        hint = f" [dim](Enter keeps {mask(current)})[/dim]" if current else ""
        while True:
            value = Prompt.ask(f"[bold]?[/bold] {question}{hint}", password=True, default="",
                               show_default=False, console=self.console).strip()
            if not value:
                if current:
                    return current
                if allow_empty:
                    return ""
                self.console.print("  [red]This one is required.[/red]")
                continue
            if confirm:
                again = Prompt.ask("  Again, to confirm", password=True, default="",
                                   show_default=False, console=self.console).strip()
                if again != value:
                    self.console.print("  [red]The two did not match.[/red]")
                    continue
            return value

    def confirm(self, key, question, default=True):
        return Confirm.ask(f"[bold]?[/bold] {question}", default=default, console=self.console)


class AnswersAsker(Asker):
    """Answers from a JSON document, by dotted key; defaults fill the gaps."""

    def __init__(self, console: Console, answers: dict) -> None:
        super().__init__(console)
        self.answers = answers

    @classmethod
    def from_file(cls, console: Console, path: str) -> "AnswersAsker":
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SetupError(f"could not read the answers file {path}: {exc}")
        if not isinstance(data, dict):
            raise SetupError("the answers file must hold a JSON object")
        return cls(console, data)

    _MISSING = object()

    def _get(self, key: str) -> Any:
        node: Any = self.answers
        for part in key.split("."):
            if isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            elif isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return self._MISSING
        return node

    def answered(self, key: str) -> bool:
        return self._get(key) is not self._MISSING

    def _echo(self, question: str, value: Any) -> None:
        self.console.print(f"  [dim]{question}[/dim] {value}")

    def choose(self, key, question, options, default=None):
        value = self._get(key)
        if value is self._MISSING:
            if default is None:
                raise SetupError(f"the answers file needs '{key}' (one of {', '.join(o[0] for o in options)})")
            value = default
        value = str(value)
        if value not in [o[0] for o in options]:
            raise SetupError(f"'{key}' is {value!r}; expected one of {', '.join(o[0] for o in options)}")
        self._echo(question, value)
        return value

    def multi(self, key, question, options, default=()):
        value = self._get(key)
        if value is self._MISSING:
            value = list(default)
        if isinstance(value, dict):
            value = list(value)
        if isinstance(value, str):
            value = [v for v in value.replace(",", " ").split() if v]
        allowed = [o[0] for o in options]
        bad = [v for v in value if v not in allowed]
        if bad:
            raise SetupError(f"'{key}' has {', '.join(map(str, bad))}; expected some of {', '.join(allowed)}")
        self._echo(question, ", ".join(value) or "none")
        return list(value)

    def text(self, key, question, default="", required=False, validate=None):
        value = self._get(key)
        value = default if value is self._MISSING or value is None else str(value).strip()
        if required and not value:
            raise SetupError(f"the answers file needs '{key}'")
        problem = validate(value) if validate and value else None
        if problem:
            raise SetupError(f"'{key}': {problem}")
        self._echo(question, value)
        return value

    def secret(self, key, question, current="", confirm=False, allow_empty=True):
        value = self._get(key)
        value = "" if value is self._MISSING or value is None else str(value).strip()
        if not value:
            value = current
        if not value and not allow_empty:
            raise SetupError(f"the answers file needs '{key}'")
        self._echo(question, "(set)" if value else "(empty)")
        return value

    def confirm(self, key, question, default=True):
        value = self._get(key)
        if value is self._MISSING:
            value = default
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "y", "on")
        self._echo(question, "yes" if value else "no")
        return bool(value)
