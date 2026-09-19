"""The run as one piece of prose.

A simulation leaves three artifacts and all of them are lists: turns in the
transcript, lines in the world log, rows in the tick table. Lists are how you
audit a run; they are not how you *read* one. This module compiles the same
ticks into continuous text — scene by scene, with the agents' own words as
dialogue and the world's own lines as narration — so a scenario can be read
end to end, quoted, pasted into a document, or handed to a model.

Two layers, and the order matters:

1. **The chronicle** (:func:`compose`) is assembled, not written: every
   sentence is either a template or something an agent literally said. It is
   free, instant, exact, works on a run that is still going, and cannot
   invent. It is also the only layer that must exist — a transcript nobody
   can read to the end is the problem being solved here.

2. **The retelling** (:func:`narrate`) is one model call *over the
   chronicle*, for when the reading should be a story rather than a record.
   It is optional and it costs money, which is why it is never automatic and
   why the chronicle is what it is given: narrating from the raw tick log
   would mean paying to re-derive the structure layer 1 already knows.

Everything here is pure except :func:`narrate`, which is the only part that
talks to a provider.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

# Which argument of an action carries something the agent *said*. Mirrors the
# transcript's own reading of an action, so the two surfaces never disagree
# about what counts as speech.
SPEECH_ARGS = ("text", "message", "content", "say")
ADDRESSEE_ARGS = ("agent", "to", "target", "recipient")

#: Every phrase the chronicle is built out of. Templated rather than written
#: by a model, and deliberately free of grammatical gender: an agent is named
#: by its role, and "says" has to read right whoever is behind the name.
PHRASES: Dict[str, Dict[str, str]] = {
    "en": {
        "setting": "The world as written",
        "cast": "Cast",
        "scene": "Tick {tick}",
        "thought": "{agent}, to itself: {text}",
        "says_to": "**{agent}** \u2192 *{to}*:",
        "says": "**{agent}**:",
        "acts": "**{agent}** does **{action}**{args}",
        "outcome": "The world answers: {message}",
        "refusal": "The world refuses: {message}",
        "failed": "**{agent}** loses the turn: {error}",
        "world": "Meanwhile: {text}",
        "from_outside": "From outside the world, {sender} reaches **{agent}**:",
        "silent": "Silent this tick: {names}.",
        "opening": "{env}, {activation} activation. Started {started}.",
        "activation_synchronous": "synchronous",
        "activation_triggered": "triggered",
        "finale": "How it ended",
        "ended_after": "{ticks} {tick_word}, ${cost} spent.",
        "tick_word_one": "tick", "tick_word_few": "ticks", "tick_word_many": "ticks",
        "ended_because": "It ended because: {reason}.",
        "still_running": "The run is still going — this is the chronicle so far.",
        "scores": "Objectives",
        "no_goal": "no stated goal",
        "nothing": "Nothing was recorded.",
    },
    "ru": {
        "setting": "Мир, как он записан",
        "cast": "Действующие лица",
        "scene": "Такт {tick}",
        "thought": "{agent}, про себя: {text}",
        "says_to": "**{agent}** \u2192 *{to}*:",
        "says": "**{agent}**:",
        "acts": "**{agent}** делает **{action}**{args}",
        "outcome": "Мир отвечает: {message}",
        "refusal": "Мир отказывает: {message}",
        "failed": "**{agent}** остаётся без хода: {error}",
        "world": "Тем временем: {text}",
        "from_outside": "Извне мира {sender} обращается к **{agent}**:",
        "silent": "Молчат в этом такте: {names}.",
        "opening": "{env}, активация — {activation}. Начало: {started}.",
        "activation_synchronous": "синхронная",
        "activation_triggered": "по триггерам",
        "finale": "Чем всё кончилось",
        "ended_after": "{ticks} {tick_word}, потрачено ${cost}.",
        "tick_word_one": "такт", "tick_word_few": "такта", "tick_word_many": "тактов",
        "ended_because": "Причина остановки: {reason}.",
        "still_running": "Прогон ещё идёт — это хроника на текущий момент.",
        "scores": "Цели",
        "no_goal": "цель не задана",
        "nothing": "Ничего не записано.",
    },
    "de": {
        "setting": "Die Welt, wie sie geschrieben steht",
        "cast": "Besetzung",
        "scene": "Tick {tick}",
        "thought": "{agent}, für sich: {text}",
        "says_to": "**{agent}** \u2192 *{to}*:",
        "says": "**{agent}**:",
        "acts": "**{agent}** tut **{action}**{args}",
        "outcome": "Die Welt antwortet: {message}",
        "refusal": "Die Welt verweigert: {message}",
        "failed": "**{agent}** verliert den Zug: {error}",
        "world": "Unterdessen: {text}",
        "from_outside": "Von außerhalb der Welt wendet sich {sender} an **{agent}**:",
        "silent": "Still in diesem Tick: {names}.",
        "opening": "{env}, Aktivierung: {activation}. Beginn: {started}.",
        "activation_synchronous": "synchron",
        "activation_triggered": "getriggert",
        "finale": "Wie es endete",
        "ended_after": "{ticks} {tick_word}, ${cost} ausgegeben.",
        "tick_word_one": "Tick", "tick_word_few": "Ticks", "tick_word_many": "Ticks",
        "ended_because": "Beendet, weil: {reason}.",
        "still_running": "Der Lauf läuft noch — dies ist die Chronik bis hierher.",
        "scores": "Ziele",
        "no_goal": "kein Ziel angegeben",
        "nothing": "Nichts aufgezeichnet.",
    },
}


def _phrases(lang: str) -> Dict[str, str]:
    return PHRASES.get((lang or "en")[:2].lower(), PHRASES["en"])


def _plural(n: int, p: Dict[str, str], stem: str) -> str:
    """The one/few/many form of a counted noun.

    Only Russian needs all three, but the rule is written once for everyone —
    a chronicle that says "2 тактов" reads as a machine wrote it, which is the
    one impression this whole module exists to avoid.
    """
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        key = "one"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        key = "few"
    else:
        key = "many"
    return p.get(f"{stem}_{key}", p.get(f"{stem}_many", ""))


def speech_of(action: Optional[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """``(text, addressee)`` when an action is something an agent said."""
    args = (action or {}).get("args") or {}
    key = next((k for k in SPEECH_ARGS
                if isinstance(args.get(k), str) and args[k].strip()), None)
    if not key:
        return None
    to = next((k for k in ADDRESSEE_ARGS
               if isinstance(args.get(k), str) and args[k].strip()), None)
    return str(args[key]).strip(), (str(args[to]).strip() if to else "")


def _format_args(args: Dict[str, Any], skip: Tuple[str, ...] = ()) -> str:
    pairs = [
        f"{k}={json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
        for k, v in (args or {}).items() if k not in skip
    ]
    return f" ({', '.join(pairs)})" if pairs else ""


def _quote(text: str) -> List[str]:
    """A speech as a Markdown blockquote — every line of it, blanks included,
    so a two-paragraph answer stays two paragraphs."""
    lines = str(text).strip().splitlines() or [""]
    return [f"> {line}" if line.strip() else ">" for line in lines]


def compose(run: Dict[str, Any], ticks: List[Dict[str, Any]], *,
            scenario: Optional[Dict[str, Any]] = None,
            lang: str = "en") -> str:
    """The whole run as Markdown: a cast, a scene per tick, and a finale.

    Nothing is summarised and nothing is invented — an agent's words are
    quoted, the world's lines are narrated, and the connective tissue is the
    templates above. Which means the chronicle of a run is a fact about it,
    reproducible from the log and comparable between runs.
    """
    p = _phrases(lang)
    config = run.get("config") or {}
    roles = config.get("roles") or (scenario or {}).get("roles") or []
    cast = [str(r.get("display_name") or r.get("name") or r.get("agent_id") or "?")
            for r in roles]
    known = set(cast)

    name = (scenario or {}).get("name") or config.get("name") or "Scenario"
    out: List[str] = [f"# {name}", ""]
    # Read from the run's own snapshot when the scenario is gone — a chronicle
    # of a deleted scenario is exactly the one nothing else can explain, and
    # the run kept the description it ran with.
    description = ((scenario or {}).get("description")
                   or config.get("description") or "")
    if description:
        out += [description.strip(), ""]
    # The author's own prose about the world, ahead of everything the run
    # recorded: it is the setting the scenes happen in, and the narrator reads
    # the chronicle top down. From the run's snapshot when the scenario is
    # gone, like the description above it.
    narrative = ((scenario or {}).get("narrative")
                 or config.get("narrative") or "").strip()
    if narrative:
        out += [f"## {p['setting']}", "", narrative, ""]
    activation = run.get("activation") or config.get("activation") or "synchronous"
    out += [
        "*" + p["opening"].format(
            env=run.get("environment") or (scenario or {}).get("environment") or "—",
            activation=p.get(f"activation_{activation}", activation),
            started=(run.get("started_at") or "")[:19].replace("T", " "),
        ) + "*",
        "",
    ]

    if roles:
        out += [f"## {p['cast']}", ""]
        for role, who in zip(roles, cast):
            goal = str(role.get("goal") or "").strip()
            out.append(f"- **{who}** — {goal or p['no_goal']}")
        out.append("")

    wrote_a_scene = False
    for tk in ticks:
        scene = _scene(tk, p, known)
        if not scene:
            continue
        wrote_a_scene = True
        out += [f"## {p['scene'].format(tick=tk.get('tick', 0))}", ""] + scene + [""]

    if not wrote_a_scene:
        out += [p["nothing"], ""]

    out += _finale(run, p)
    # One trailing newline, and never three in a row: the chronicle is read as
    # a document, and a document with gaps in it reads as a broken export.
    text = "\n".join(out).rstrip() + "\n"
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def _scene(tk: Dict[str, Any], p: Dict[str, str], known: set) -> List[str]:
    """One tick, in the order it happened: what came in from outside, what
    each agent thought, said and did, then what the world made of it."""
    from playground.environments.base import speech_event

    lines: List[str] = []
    decisions = sorted(tk.get("decisions") or [], key=lambda d: str(d.get("agent", "")))
    resolutions = tk.get("resolutions") or []
    # Every delivered message is also a line in the world's log, which is right
    # for a log and wrong for a scene: quoting the dialogue and then narrating
    # the same words as an event says everything twice. The lines are rebuilt
    # with the environment's own formatter, so this recognises exactly the
    # echoes it should and nothing else.
    echoes: set = set()

    for d in decisions:
        agent = str(d.get("agent") or "?")

        # Mail from outside the cast has no turn of its own anywhere in the
        # run — an operator's poke, a webhook. Unnarrated, the agent's reply
        # would answer a question the reader never saw asked.
        for msg in (d.get("observation") or {}).get("messages") or []:
            if not isinstance(msg, dict):
                continue
            sender = str(msg.get("from") or "")
            if sender and sender not in known:
                lines += [p["from_outside"].format(sender=sender, agent=agent), ""]
                lines += _quote(msg.get("text") or "") + [""]
                echoes.add(speech_event(sender, agent, msg.get("text") or ""))

        if d.get("error"):
            lines += [p["failed"].format(agent=agent, error=d["error"]), ""]
            continue

        reasoning = str(d.get("reasoning") or "").strip()
        if reasoning:
            lines += ["*" + p["thought"].format(
                agent=agent, text=" ".join(reasoning.split())) + "*", ""]

        action = d.get("action") or {}
        said = speech_of(action)
        if said:
            text, to = said
            head = (p["says_to"].format(agent=agent, to=to) if to
                    else p["says"].format(agent=agent))
            lines += [head, ""] + _quote(text) + [""]
            if to:
                echoes.add(speech_event(agent, to, text))
        elif action.get("action"):
            lines += [p["acts"].format(
                agent=agent, action=action["action"],
                args=_format_args(action.get("args") or {}),
            ) + ".", ""]

        # What the world made of it, in the same breath as the action — a
        # refusal read three paragraphs later is a different sentence.
        for res in resolutions:
            if str(res.get("agent") or "") != agent:
                continue
            message = str(res.get("message") or "").strip()
            if not message or (said and res.get("ok")):
                # A delivered message needs no receipt: the words are above.
                continue
            key = "outcome" if res.get("ok") else "refusal"
            lines += [p[key].format(message=message), ""]

    for event in tk.get("events") or []:
        if str(event) in echoes:
            continue
        lines += ["*" + p["world"].format(text=str(event)) + "*", ""]

    idle = tk.get("idle") or []
    if idle and decisions:
        lines += [f"*{p['silent'].format(names=', '.join(idle))}*", ""]
    return lines


def _finale(run: Dict[str, Any], p: Dict[str, str]) -> List[str]:
    out = [f"## {p['finale']}", ""]
    if run.get("status") in ("starting", "running", "stopping"):
        out += [p["still_running"], ""]
    done = int(run.get("ticks_done") or 0)
    out.append(p["ended_after"].format(
        ticks=done, tick_word=_plural(done, p, "tick_word"),
        cost=f"{float(run.get('total_cost') or 0.0):.4f}",
    ))
    if run.get("stop_reason"):
        out.append(p["ended_because"].format(reason=run["stop_reason"]))
    if run.get("error"):
        out.append(f"`{run['error']}`")
    out.append("")
    scores = run.get("scores") or {}
    if scores:
        out += [f"### {p['scores']}", ""]
        for objective, values in scores.items():
            detail = (", ".join(f"{k}: {v}" for k, v in values.items())
                      if isinstance(values, dict) else str(values))
            out.append(f"- **{objective}** — {detail}")
        out.append("")
    return out


# ── The optional second layer ────────────────────────────────────────────────

#: What the model is asked to do with the chronicle. Deliberately narrow: it
#: is retelling a record, not writing fiction from a premise. Every constraint
#: here exists because the alternative is a pretty page that says something
#: the run never did — and a retelling nobody can trust is worth less than the
#: chronicle it was made from.
NARRATOR_SYSTEM = """You are a chronicler retelling a multi-agent simulation \
as a piece of continuous prose.

Rules, in order of importance:
1. Invent nothing. Every event, line of dialogue, refusal and outcome must \
come from the chronicle you are given. If the agents never met, they never met.
2. Keep what was said as said. You may weave quotes into the narration and \
trim filler, but never put words in an agent's mouth.
3. Write it as a scene, not as minutes: connect the ticks into a flowing \
account with a beginning, the turns it took, and how it ended. Drop the tick \
numbering.
4. Keep the agents' names exactly as written.
5. The chronicle may open with an authored section about the setting, before \
the first scene. It is background, not events: draw the world's history, rules \
and atmosphere from it, and never report any of it as something that happened \
during the run.
6. Say plainly when a run was cut short, went nowhere, or ended in failure. A \
dull run is a dull story; do not rescue it with invention.
7. Markdown, a title and a few sections, no more than about 700 words unless \
the chronicle is much longer than that.

Write in {language}."""

_LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "de": "German"}


#: How much of the half-written retelling the page is shown. The window is
#: five lines and it is the *tail* that matters, so a few hundred characters
#: is generous; sending the whole draft on every beat would be a new copy of
#: the text several times a second.
TAIL_CHARS = 800

#: Seconds between progress events. Fast enough that the text visibly moves,
#: slow enough that a long retelling is not a flood.
PROGRESS_EVERY = 0.35


def narrate(chronicle: str, *, run: Dict[str, Any],
            scenario: Optional[Any] = None, workspace: Optional[str] = None,
            lang: str = "en", sim_run_id: str = "") -> Dict[str, Any]:
    """One model call that turns the chronicle into a story.

    Uses the scenario's own default model — the same resolution every agent in
    the run went through — so the retelling is priced, logged and configured
    exactly like the run it describes. Raises on failure: the caller is a
    request the user made and pressed a button for, and a silent empty result
    would be indistinguishable from a model with nothing to say.

    Streamed when the client can, and not for the timeout's sake: the page
    shows the draft as it is written, because a paid call that takes half a
    minute has to look like work rather than like a hung button. The stream is
    published to the run's own channel as ``story_delta`` — the tail of what
    has been written so far — and ends with ``story_done``. When the finished
    text arrives the page drops the draft and shows only the result.
    """
    from playground.models import Role, Scenario
    from playground.runner import _publish, _run_cost, _text_of, _usage_of, resolve_model

    spec = scenario if isinstance(scenario, Scenario) else Scenario()
    provider, model = resolve_model(Role(), spec, workspace)
    language = _LANGUAGE_NAMES.get((lang or "en")[:2].lower(), "English")

    from agents.agent_utils import build_chat_model
    llm = build_chat_model(provider=provider or None, model=model or None,
                           temperature=0.8, streaming=True)
    messages = [
        ("system", NARRATOR_SYSTEM.format(language=language)),
        ("human", chronicle),
    ]

    state = {"at": 0.0}

    def report(text: str, *, force: bool = False) -> None:
        if not sim_run_id:
            return
        now = time.monotonic()
        if not force and now - state["at"] < PROGRESS_EVERY:
            return
        state["at"] = now
        _publish(sim_run_id, {"type": "story_delta", "text": text[-TAIL_CHARS:],
                              "chars": len(text)})

    reply = None
    text = ""
    if sim_run_id:
        _publish(sim_run_id, {"type": "story_start"})
    try:
        stream = getattr(llm, "stream", None)
        if callable(stream):
            parts: List[str] = []
            try:
                for chunk in stream(messages):
                    parts.append(_text_of(chunk))
                    try:
                        reply = chunk if reply is None else reply + chunk
                    except Exception:  # noqa: BLE001 — not every chunk adds up
                        reply = None
                    report("".join(parts))
                text = "".join(parts)
            except NotImplementedError:
                # Advertised but not implemented. Nothing was spent, so falling
                # through to invoke() does not pay for the call twice.
                text, reply = "", None
        if not text:
            reply = llm.invoke(messages)
            text = _text_of(reply)
    finally:
        if sim_run_id:
            _publish(sim_run_id, {"type": "story_done"})

    usage = _usage_of(reply)
    return {
        "text": str(text or "").strip(),
        "provider": provider, "model": model,
        "inbound_tokens": usage["inbound"], "outbound_tokens": usage["outbound"],
        "cost": _run_cost(provider, model, usage["inbound"], usage["outbound"]),
        "lang": (lang or "en")[:2].lower(),
    }
