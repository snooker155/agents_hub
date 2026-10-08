"""
A schedule from a phrase, without a model: "every morning at 8", "every
weekday at 9:30", "каждое утро в 8", "jeden Montag um 7 Uhr" (docs/proactive.md
"From a phrase").

:func:`parse_schedule` reads the common English, Russian and German shapes and
returns a cron expression with its plain-words rendering, or ``None`` when the
phrase is not one it knows; the assistant's tool (tools/proactive_setup.py)
then takes a cron expression the model wrote and checks it with
:func:`check_cron`. :func:`describe_cron` turns any cron expression back into
a sentence ("Every day at 08:00 Europe/Berlin"), the line the confirmation
card shows, so the person approves what will actually run.

Deliberately small: a time of day with a frequency (daily, weekdays, weekends,
named weekdays, a day of the month) or an interval (every N minutes or hours).
Anything else is the model's job and goes through cron validation.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: The shortest interval a pulse may tick at (profile.INTERVAL_MINUTES starts here).
MIN_INTERVAL_MINUTES = 5

LANGS = ("en", "ru", "de")


@dataclass(frozen=True)
class Schedule:
    cron: str
    #: ``en``, ``ru`` or ``de``: the language the phrase was in.
    lang: str = "en"


# ── words ────────────────────────────────────────────────────────────────────

#: Weekday numbers as cron counts them (0 Sunday), with the stems each language uses.
_DAY_WORDS: Dict[int, Tuple[str, ...]] = {
    1: ("monday", "mon", "понедельник", "пн", "montag", "montags", "mo"),
    2: ("tuesday", "tue", "tues", "вторник", "вт", "dienstag", "dienstags", "di"),
    3: ("wednesday", "wed", "среда", "среду", "средам", "ср", "mittwoch", "mittwochs", "mi"),
    4: ("thursday", "thu", "thur", "thurs", "четверг", "чт", "donnerstag", "donnerstags", "do"),
    5: ("friday", "fri", "пятница", "пятницу", "пт", "freitag", "freitags", "fr"),
    6: ("saturday", "sat", "суббота", "субботу", "сб", "samstag", "samstags", "sa"),
    0: ("sunday", "sun", "воскресенье", "воскресенья", "вс", "sonntag", "sonntags", "so"),
}
#: Plural and "on Mondays" forms: a stem match is enough for the long names.
_DAY_STEMS: Dict[int, Tuple[str, ...]] = {
    1: ("monday", "понедельник", "montag"),
    2: ("tuesday", "вторник", "dienstag"),
    3: ("wednesday", "сред", "mittwoch"),
    4: ("thursday", "четверг", "donnerstag"),
    5: ("friday", "пятниц", "freitag"),
    6: ("saturday", "суббот", "samstag"),
    0: ("sunday", "воскресен", "sonntag"),
}

_WEEKDAYS = re.compile(
    r"\b(weekdays?|work ?days?|business days?|monday (?:to|through|thru|-) friday|"
    r"по будн\w*|в будн\w*|рабочим дням|в рабочие дни|с понедельника по пятницу|"
    r"werktags|an werktagen|wochentags|montags? bis freitags?|montag bis freitag)\b")
_WEEKENDS = re.compile(
    r"\b(weekends?|по выходным|в выходные|am wochenende|wochenends|an wochenenden)\b")
_DAILY = re.compile(
    r"\b(every ?day|daily|each day|every (?:morning|evening|night|noon|afternoon)|"
    r"each (?:morning|evening)|every day|"
    r"каждый день|ежедневно|каждое утро|каждый вечер|каждую ночь|по утрам|по вечерам|"
    r"täglich|jeden tag|jeden morgen|jeden abend|jede nacht|jeden mittag|morgens|abends)\b")

#: A part of the day with the hour that stands for it when no hour is said.
_DAYPARTS: Tuple[Tuple[re.Pattern, int], ...] = tuple((re.compile(p), h) for p, h in (
    (r"\b(morning|утро|утром|утра|по утрам|morgen|morgens|früh)\b", 8),
    (r"\b(noon|midday|полдень|в полдень|mittag|mittags)\b", 12),
    (r"\b(afternoon|днем|днём|после обеда|nachmittag|nachmittags)\b", 15),
    (r"\b(evening|вечер|вечером|по вечерам|abend|abends)\b", 18),
    (r"\b(night|ночью|ночь|nacht|nachts)\b", 22),
))

_INTERVAL = re.compile(
    r"(?:every|each|alle|jede[rnms]?|каждые|каждый|каждую|раз в)\s*"
    r"(\d+)?\s*(minutes?|mins?|hours?|hrs?|минут\w*|мин|час\w*|ч|stunden?|std|minuten?)\b")
_INTERVAL_HALF = re.compile(r"\b(every half[- ]hour|alle halbe stunde|каждые полчаса|каждые пол часа|jede halbe stunde)\b")

_MONTH_DAY = re.compile(
    r"(?:on the |am |on )?(\d{1,2})(?:st|nd|rd|th|\.|-го|го)?\s*(?:of (?:every|each) month|"
    r"(?:числа )?каждого месяца|jeden monats|des monats|jedes monats)"
    r"|каждое\s+(\d{1,2})\s*(?:-е)?\s*число|ежемесячно\s+(\d{1,2})\s*числа")

_TIME = re.compile(
    r"(?<![\d:.])(?:(?:at|um|в|к|ab|gegen|around)\s+)?(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.|uhr|час\w*|ч\b)?")


def _lang_of(text: str) -> str:
    if re.search(r"[а-яё]", text):
        return "ru"
    if re.search(r"\b(jede[nrms]?|täglich|um|uhr|alle|werktags|montags|morgens|abends|stunden?|minuten?|am wochenende)\b|[äöüß]", text):
        return "de"
    return "en"


def _time_of(text: str, *, skip: Tuple[Tuple[int, int], ...] = ()) -> Optional[Tuple[int, int]]:
    """The hour and minute named in *text*, or None. A bare number only counts
    when a time word (at, um, в) or a clock shape (9:30, 8 Uhr, 8pm) goes with it."""
    for m in _TIME.finditer(text):
        if any(a <= m.start(1) < b for a, b in skip):
            continue
        lead = text[m.start():m.start(1)].strip()
        hour, minute, suffix = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
        if not (lead or minute or suffix):
            continue
        mins = int(minute) if minute else 0
        if suffix in ("pm", "p.m.") and hour < 12:
            hour += 12
        elif suffix in ("am", "a.m.") and hour == 12:
            hour = 0
        if hour > 23 or mins > 59:
            continue
        return hour, mins
    return None


def _days_of(text: str) -> List[int]:
    found: List[int] = []
    for num, stems in _DAY_STEMS.items():
        if any(s in text for s in stems):
            found.append(num)
            continue
        short = _DAY_WORDS[num]
        if any(re.search(rf"\b{re.escape(w)}\b", text) for w in short if len(w) > 2):
            found.append(num)
    return sorted(found, key=lambda d: (d + 6) % 7)


def _dow_field(days: List[int]) -> str:
    order = sorted(days, key=lambda d: (d + 6) % 7)
    # Monday to Friday and the like collapse into a range; the rest stay a list.
    seq = [(d + 6) % 7 for d in order]
    if len(seq) > 2 and seq == list(range(seq[0], seq[0] + len(seq))) and seq[-1] <= 6:
        return f"{order[0]}-{order[-1]}" if order[0] <= order[-1] else ",".join(str(d) for d in order)
    return ",".join(str(d) for d in order)


def parse_schedule(text: str) -> Optional[Schedule]:
    """The schedule a phrase asks for, or None when it is not one of the known
    shapes. Raises ``ValueError`` for a phrase that is clear but not allowed
    (an interval under :data:`MIN_INTERVAL_MINUTES`)."""
    src = " ".join(str(text or "").lower().replace("ё", "е").split())
    if not src:
        return None
    lang = _lang_of(str(text or "").lower())

    if _INTERVAL_HALF.search(src):
        return Schedule("*/30 * * * *", lang)
    m = _INTERVAL.search(src)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2)
        hours = unit.startswith(("hour", "hr", "час", "ч", "stund", "std"))
        if n < 1:
            return None
        if hours:
            if n >= 24:
                return Schedule("0 0 * * *", lang) if n == 24 else None
            return Schedule("0 * * * *" if n == 1 else f"0 */{n} * * *", lang)
        if n < MIN_INTERVAL_MINUTES:
            raise ValueError(f"A pulse ticks at most every {MIN_INTERVAL_MINUTES} minutes.")
        if n >= 60:
            return Schedule("0 * * * *" if n == 60 else f"0 */{n // 60} * * *", lang) if n % 60 == 0 else None
        return Schedule(f"*/{n} * * * *", lang)

    month_day = _MONTH_DAY.search(src)
    day_skip: Tuple[Tuple[int, int], ...] = ((month_day.start(), month_day.end()),) if month_day else ()
    clock = _time_of(src, skip=day_skip)
    if clock is None:
        for pattern, hour in _DAYPARTS:
            if pattern.search(src):
                clock = (hour, 0)
                break

    days = _days_of(src)
    if month_day:
        dom = int(next(g for g in month_day.groups() if g))
        if not 1 <= dom <= 31 or clock is None:
            return None
        return Schedule(f"{clock[1]} {clock[0]} {dom} * *", lang)
    if clock is None:
        return None
    minute, hour = clock[1], clock[0]
    if _WEEKDAYS.search(src):
        return Schedule(f"{minute} {hour} * * 1-5", lang)
    if _WEEKENDS.search(src):
        return Schedule(f"{minute} {hour} * * 0,6", lang)
    if days:
        return Schedule(f"{minute} {hour} * * {_dow_field(days)}", lang)
    if _DAILY.search(src) or re.search(r"\b(every|each|jeden|каждый|ежедневно)\b", src):
        return Schedule(f"{minute} {hour} * * *", lang)
    return None


# ── cron back to words ───────────────────────────────────────────────────────

_DAY_NAMES: Dict[str, Tuple[str, ...]] = {
    "en": ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"),
    "ru": ("воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"),
    "de": ("Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"),
}


def _dow_list(field: str) -> Optional[List[int]]:
    days: List[int] = []
    for part in field.split(","):
        if re.fullmatch(r"\d", part):
            days.append(int(part) % 7)
        elif re.fullmatch(r"\d-\d", part):
            a, b = (int(x) for x in part.split("-"))
            days.extend(d % 7 for d in range(a, b + 1))
        else:
            return None
    return sorted(set(days))


def check_cron(expr: str) -> str:
    """The expression as a five-field cron string, or ``ValueError``. Also
    refuses what would tick more often than :data:`MIN_INTERVAL_MINUTES`."""
    from croniter import croniter
    text = " ".join(str(expr or "").split())
    if len(text.split()) != 5:
        raise ValueError(f"A cron expression has five fields (minute hour day month weekday), got '{text}'.")
    if not croniter.is_valid(text):
        raise ValueError(f"'{text}' is not a valid cron expression.")
    from datetime import datetime, timezone
    it = croniter(text, datetime(2030, 1, 1, tzinfo=timezone.utc))
    first, second = it.get_next(datetime), it.get_next(datetime)
    if (second - first).total_seconds() < MIN_INTERVAL_MINUTES * 60:
        raise ValueError(f"A pulse ticks at most every {MIN_INTERVAL_MINUTES} minutes; '{text}' is faster.")
    return text


def describe_cron(cron: str, timezone_name: str = "UTC", lang: str = "en") -> str:
    """A plain sentence for a cron expression ("Every day at 08:00 Europe/Berlin").
    Shapes this module does not recognise are shown as the expression itself."""
    lang = lang if lang in LANGS else "en"
    fields = str(cron or "").split()
    tz = timezone_name or "UTC"
    raw = {"en": f"On the schedule {cron}", "ru": f"По расписанию {cron}", "de": f"Nach dem Zeitplan {cron}"}[lang]
    if len(fields) != 5:
        return f"{raw} ({tz})"
    minute, hour, dom, month, dow = fields
    at = f"{int(hour):02d}:{int(minute):02d}" if hour.isdigit() and minute.isdigit() else None

    if month == "*" and dom == "*" and dow == "*":
        if at:
            return {"en": f"Every day at {at} {tz}", "ru": f"Каждый день в {at} ({tz})",
                    "de": f"Jeden Tag um {at} Uhr ({tz})"}[lang]
        m = re.fullmatch(r"\*/(\d+)", minute)
        if m and hour == "*":
            n = m.group(1)
            return {"en": f"Every {n} minutes", "ru": f"Каждые {n} минут", "de": f"Alle {n} Minuten"}[lang]
        if minute.isdigit() and hour == "*":
            return {"en": f"Every hour at minute {int(minute):02d} ({tz})",
                    "ru": f"Каждый час, в {int(minute):02d} минут ({tz})",
                    "de": f"Jede Stunde zur Minute {int(minute):02d} ({tz})"}[lang]
        m = re.fullmatch(r"\*/(\d+)", hour)
        if m and minute.isdigit():
            n = m.group(1)
            return {"en": f"Every {n} hours ({tz})", "ru": f"Каждые {n} ч ({tz})", "de": f"Alle {n} Stunden ({tz})"}[lang]
    if month == "*" and dom == "*" and at and dow != "*":
        days = _dow_list(dow)
        if days == [1, 2, 3, 4, 5]:
            return {"en": f"Every weekday at {at} {tz}", "ru": f"По будням в {at} ({tz})",
                    "de": f"Werktags um {at} Uhr ({tz})"}[lang]
        if days == [0, 6]:
            return {"en": f"Every weekend day at {at} {tz}", "ru": f"По выходным в {at} ({tz})",
                    "de": f"Am Wochenende um {at} Uhr ({tz})"}[lang]
        if days:
            names = _DAY_NAMES[lang]
            if lang == "ru":
                listed = ", ".join(names[d] for d in days)
                return f"Каждую неделю: {listed}, в {at} ({tz})"
            if lang == "de":
                return f"Jede Woche am {' und '.join(names[d] for d in days)} um {at} Uhr ({tz})"
            return f"Every {' and '.join(names[d] for d in days)} at {at} {tz}"
    if month == "*" and dow == "*" and at and dom.isdigit():
        return {"en": f"On day {int(dom)} of every month at {at} {tz}",
                "ru": f"{int(dom)} числа каждого месяца в {at} ({tz})",
                "de": f"Am {int(dom)}. jeden Monats um {at} Uhr ({tz})"}[lang]
    return f"{raw} ({tz})"


def server_timezone() -> str:
    """The server's IANA timezone name, UTC when it cannot be found."""
    from zoneinfo import ZoneInfo
    candidates: List[str] = [os.environ.get("TZ", "")]
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            candidates.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        name = name.strip().lstrip(":")
        if name:
            try:
                ZoneInfo(name)
                return name
            except Exception:  # noqa: BLE001 - an unknown name just means try the next source
                continue
    return "UTC"


__all__ = ["MIN_INTERVAL_MINUTES", "Schedule", "check_cron", "describe_cron",
           "parse_schedule", "server_timezone"]
