"""Single source of truth for the system prompt.

The agent and the cache warmer MUST produce a byte-identical prefix - OpenAI
keys its prompt cache on that prefix, so any drift silently creates a second
cache and the warming stops working. Hence one function, imported by both.
"""
from __future__ import annotations

import datetime
import logging
import re
import zoneinfo

import hours

log = logging.getLogger("prompt")

# Two sets, because the section above them is two different things.
#
# In FULL mode the documents are in the prompt. In INDEX mode - which is what a
# knowledge base of any size ends up in - only the TITLES are there, and the
# model has to search to see a word of the content.
#
# There used to be one set, and it opened with "the REFERENCE INFORMATION
# section above is your primary source; answer from it." In index mode that
# section is called AVAILABLE DOCUMENTS and holds nothing but titles, so the
# first and most important rule pointed at something that did not exist.
#
# Call 538 is what that looks like: thirteen turns about buying a motorcycle,
# ZERO searches, and a flat "Splendor Plus does not have i3s" that came out of
# the model's training rather than out of the customer's documents. It happened
# to be right. Nothing in the process made it so.

GROUNDING_FULL = """

KNOWLEDGE RULES - these override every other instruction:
- The "REFERENCE INFORMATION" section above is your primary source. Answer from it.
- If it does not answer the question, call search_knowledge_base once, then answer
  from what it returns.
- If neither has the answer, say plainly that you do not have that information.
- Never invent or guess a price, date, phone number, policy, name, or availability.
  A confident wrong number is far worse than admitting you do not know - the caller
  will act on it.
- Do not fill gaps with general knowledge.
"""

GROUNDING_INDEX = """

KNOWLEDGE RULES - these override every other instruction:
- The "AVAILABLE DOCUMENTS" section above is a LIST OF TITLES. It does not
  contain the documents. You have not read any of them.
- Before stating ANY fact about a product - a price, a specification, a feature,
  whether something is available, an offer, a policy - call
  search_knowledge_base first. Knowing a title is not knowing what is in it.
- Answer only from what the search returns. If it returns nothing useful, say
  plainly that you do not have that information and offer to find out.
- Never answer a product question from your own knowledge, even when you are
  confident. You may be describing a different model, a different year or a
  different market, and the caller will act on it.
- Never invent or guess a price, date, phone number, policy, name, or availability.
"""

TRANSFER_RULES = """

HANDOFF:
- Call transfer_to_human when the caller asks for a person, sounds frustrated,
  wants to complain, or asks something you still cannot answer after searching.
- Do NOT transfer for anything you can answer yourself.
- Do not announce the handoff yourself - the tool speaks the line and moves the
  call. Just call it.
"""


# {{cus_name}} / {{modalname|आपकी गाड़ी}} in anything spoken to the caller.
#
# A default after the pipe is not decoration. The dialler does not always send
# every field - X-language arrives empty today - and a greeting that renders as
# "क्या मेरी बात  जी से हो रही है?" is worse than one that never used the name.
# Without a default the placeholder becomes empty and the double space is
# collapsed, which is the least bad of the remaining options.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*(?:\|([^}]*))?\}\}")


def render_spoken(template: str | None, dialler: dict[str, str]) -> str | None:
    """Substitute dialler context into a spoken string.

    Only ever applied to things SAID to the caller - greeting, transfer and
    limit messages. Never to `instructions`: those are the cacheable prompt
    prefix, and a caller's name inside them would make every call's prefix
    unique and silently kill the prompt cache.
    """
    if not template or "{{" not in template:
        return template

    def one(m: re.Match) -> str:
        key, default = m.group(1), (m.group(2) or "")
        return (dialler.get(f"dialer.{key}") or default).strip()

    return re.sub(r"\s{2,}", " ", _PLACEHOLDER.sub(one, template)).strip()


def now_line(tz_name: str | None) -> str:
    """The one line that tells the agent what day it is.

    Spelled out - weekday, month by name, 12-hour clock - because the model has
    to reason with it ("कल" means tomorrow's date, not the string "tomorrow") and
    an ISO stamp invites it to read the digits aloud to the caller.

    An unknown timezone falls back to +05:30, not to UTC. Every caller on this
    system is in India, and a clock silently five and a half hours out looks
    like it is working right up until somebody books a morning appointment.
    """
    tz = None
    if tz_name:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            log.warning("unknown timezone %r - using +05:30", tz_name)
    if tz is None:
        tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
    now = datetime.datetime.now(tz)
    return ("CURRENT DATE AND TIME: "
            + now.strftime("%A, %d %B %Y, %I:%M %p ").replace(" 0", " ")
            + (tz_name or "IST")
            + "\nUse this to work out what the caller means by today, "
              "tomorrow, this evening, next week and so on.")


async def build_instructions(cfg) -> tuple[str, str, int]:
    """-> (instructions, kb_mode, kb_tokens)"""
    import kb

    instructions = cfg.instructions
    kb_mode, kb_tokens = "off", 0
    if cfg.kb_enabled:
        text, kb_tokens, kb_mode = await kb.load_inline(
            cfg.name, cfg.kb_inline_max_tokens)
        if text:
            label = ("REFERENCE INFORMATION" if kb_mode == "full" else
                     "AVAILABLE DOCUMENTS (use search_knowledge_base for details)")
            instructions += f"\n\n=== {label} ===\n{text}\n=== END ===\n"
        # The rules have to describe the section that was actually written
        # above them - see the note beside them.
        instructions += GROUNDING_FULL if kb_mode == "full" else GROUNDING_INDEX
    if cfg.transfer_enabled:
        instructions += TRANSFER_RULES
        # The hours are enforced in the agent whatever this says. This is here
        # so the model does not tell a caller at 9pm that it is connecting
        # them and then get refused - the rule works and the call still sounds
        # broken.
        #
        # Safe for the cache warmer: it reads stored config and never the
        # clock, so one campaign produces the same bytes all day.
        window = hours.summary(cfg)
        if window:
            instructions += "\n" + window + "\n"
    return instructions, kb_mode, kb_tokens
