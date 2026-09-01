"""When a human is available to take a transfer, and how to say so.

Kept out of voice_agent.py because it is the kind of code that is wrong in
ways nobody notices for a month - the Sunday that is closed, the holiday that
is a string not a date, the 18:30 that is the server's 18:30 and not the
caller's. It is small enough to read in one sitting and pure enough to test
without a phone call.

Everything here works in the campaign's timezone. The server's clock is never
consulted for anything but "now", and even that is converted immediately.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("voice-agent")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Spoken forms, not written ones. A Hindi campaign saying "Monday" in the
# middle of a Hindi sentence sounds like a machine reading a spreadsheet.
#
# Latin rather than Devanagari, to match every other Hindi string in this
# system - the greeting, the transfer message, the silence prompts. The first
# version of this was in Devanagari and produced sentences with two scripts in
# them: "Abhi hamari team available nahi hai. kal 9:30 baje se..." reads as one
# sentence; the same line with two alphabets does not.
#
# Only the languages the campaigns actually run in. Anything else falls back to
# English, which is wrong but understandable - the alternative is a day name in
# a language nobody chose.
_DAY_NAMES = {
    "hi": ("somvar", "mangalvar", "budhvar", "guruvar",
           "shukravar", "shanivar", "ravivar"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday",
           "Friday", "Saturday", "Sunday"),
}
_TODAY = {"hi": "aaj", "en": "today"}
_TOMORROW = {"hi": "kal", "en": "tomorrow"}
_AT = {"hi": "{when} {time} baje", "en": "{when} at {time}"}

# Used when the campaign left the message blank. A caller who asks for a person
# should never get silence because a field was not filled in.
_FALLBACK = {
    "hi": "Abhi hamari team available nahi hai. {next_open} se koi aapse baat "
          "kar sakega.",
    "en": "Our team is not available right now. Someone can speak with you "
          "{next_open}.",
}

# How far ahead to look for the next open slot. A campaign closed for a
# fortnight of holidays is a mistake, not a schedule, and saying "not available"
# is better than scanning a year to find out.
_HORIZON_DAYS = 14


def _zone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "Asia/Kolkata")
    except (ZoneInfoNotFoundError, ValueError):
        # A bad timezone must not decide that the team is unavailable. Falling
        # back is the safer error: at worst the hours are an hour off, rather
        # than every caller being told nobody is there.
        logger.warning("unknown timezone %r - using Asia/Kolkata", tz_name)
        return ZoneInfo("Asia/Kolkata")


def _lang(language: str | None) -> str:
    return "hi" if (language or "").lower().startswith("hi") else "en"


def _parse_hhmm(value) -> time | None:
    """"09:30" -> time(9, 30). Anything else -> None, and it is logged.

    Returning None rather than raising is deliberate: a malformed row should
    close that ONE day, not take the whole call down at the moment somebody
    asked for help.
    """
    if not isinstance(value, str):
        return None
    try:
        hh, _, mm = value.partition(":")
        return time(int(hh), int(mm))
    except (TypeError, ValueError):
        logger.warning("transfer_hours: cannot read time %r", value)
        return None


def _holidays(raw) -> dict[date, str]:
    """[{"date": "2026-10-20", "label": "Diwali"}] -> {date: label}."""
    out: dict[date, str] = {}
    for item in raw or ():
        if not isinstance(item, dict):
            continue
        try:
            out[date.fromisoformat(item["date"])] = item.get("label") or ""
        except (KeyError, TypeError, ValueError):
            logger.warning("transfer_holidays: cannot read %r", item)
    return out


def _window(hours: dict, day: date) -> tuple[time, time] | None:
    """The open and close time for one day, or None if closed."""
    raw = (hours or {}).get(DAYS[day.weekday()])
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    start, end = _parse_hhmm(raw[0]), _parse_hhmm(raw[1])
    if start is None or end is None or start >= end:
        # An open that is not before its close is not a window. Overnight
        # shifts would need two rows and the form does not offer them, so this
        # is a mistake rather than a night shift.
        return None
    return start, end


def is_open(cfg, now: datetime | None = None) -> tuple[bool, str | None]:
    """Can this campaign transfer right now?

    Returns (open, reason). The reason is only set when closed, and is what
    gets written to calls.transfer_refused - 'holiday' and 'closed' are worth
    telling apart when somebody later asks why the callback list is long.
    """
    if not getattr(cfg, "transfer_hours_enabled", False):
        return True, None

    hours = getattr(cfg, "transfer_hours", None) or {}
    if not hours:
        # Enabled with nothing configured would otherwise mean "never", which
        # is a config mistake silently becoming a policy. Open is the safer
        # reading, and the console warns about it.
        logger.warning("transfer hours are on but no days are set - allowing")
        return True, None

    local = (now or datetime.now(_zone(cfg.prompt_timezone))).astimezone(
        _zone(cfg.prompt_timezone))

    if local.date() in _holidays(getattr(cfg, "transfer_holidays", None)):
        return False, "holiday"

    window = _window(hours, local.date())
    if window is None:
        return False, "closed"
    if window[0] <= local.time() < window[1]:
        return True, None
    return False, "closed"


def next_open(cfg, now: datetime | None = None) -> datetime | None:
    """When the team is next available, or None within the horizon."""
    zone = _zone(cfg.prompt_timezone)
    local = (now or datetime.now(zone)).astimezone(zone)
    hours = getattr(cfg, "transfer_hours", None) or {}
    holidays = _holidays(getattr(cfg, "transfer_holidays", None))

    for offset in range(_HORIZON_DAYS):
        day = local.date() + timedelta(days=offset)
        if day in holidays:
            continue
        window = _window(hours, day)
        if window is None:
            continue
        opens = datetime.combine(day, window[0], tzinfo=zone)
        # Today counts only if it has not already opened and closed.
        if opens > local:
            return opens
        if offset == 0 and window[0] <= local.time() < window[1]:
            return local
    return None


def describe(when: datetime | None, language: str | None,
             now: datetime | None = None) -> str:
    """"kal 9:30 बजे" - the phrase that replaces {next_open}.

    Deliberately built from a fixed table rather than a locale library: the
    output is spoken aloud by TTS, and 'Mon 09:30' read out is worse than
    nothing. Today and tomorrow are named rather than dated because that is
    how a person says it.
    """
    lang = _lang(language)
    if when is None:
        return {"hi": "baad me", "en": "later"}[lang]

    today = (now or datetime.now(when.tzinfo)).astimezone(when.tzinfo).date()
    delta = (when.date() - today).days
    if delta == 0:
        day_word = _TODAY[lang]
    elif delta == 1:
        day_word = _TOMORROW[lang]
    else:
        day_word = _DAY_NAMES[lang][when.weekday()]

    hour = when.hour % 12 or 12
    clock = f"{hour}:{when.minute:02d}" if when.minute else str(hour)
    return _AT[lang].format(when=day_word, time=clock)


def closed_message(cfg, now: datetime | None = None) -> str:
    """What the agent says when it cannot hand the call over."""
    lang = _lang(getattr(cfg, "language", None))
    template = (getattr(cfg, "transfer_closed_message", None) or "").strip()
    if not template:
        template = _FALLBACK[lang]
    return template.replace(
        "{next_open}",
        describe(next_open(cfg, now), getattr(cfg, "language", None), now))


def summary(cfg) -> str | None:
    """One line for the prompt, so the model does not promise what it cannot do.

    The code refuses the transfer either way. This exists so the refusal does
    not arrive after the model has already told the caller it is connecting
    them, which sounds broken even though the rule worked.
    """
    if not getattr(cfg, "transfer_hours_enabled", False):
        return None
    hours = getattr(cfg, "transfer_hours", None) or {}
    if not hours:
        return None

    lang = _lang(getattr(cfg, "language", None))
    names = _DAY_NAMES["en"]  # the prompt is read by the model, not the caller
    parts = []
    for i, key in enumerate(DAYS):
        raw = hours.get(key)
        if raw and isinstance(raw, (list, tuple)) and len(raw) == 2:
            parts.append(f"{names[i][:3]} {raw[0]}-{raw[1]}")
    if not parts:
        return None

    tz = getattr(cfg, "prompt_timezone", None) or "Asia/Kolkata"
    line = ("A human colleague is only available " + ", ".join(parts)
            + f" ({tz}). Outside those hours a transfer is not possible - do "
              "not promise to connect the caller to a person; say when the "
              "team is next available instead.")
    if lang == "hi":
        line += " Say it in the caller's language."
    return line
