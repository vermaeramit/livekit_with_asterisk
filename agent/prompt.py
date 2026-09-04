"""Single source of truth for the system prompt.

The agent and the cache warmer MUST produce a byte-identical prefix - OpenAI
keys its prompt cache on that prefix, so any drift silently creates a second
cache and the warming stops working. Hence one function, imported by both.
"""
from __future__ import annotations

import re

import hours

GROUNDING_RULES = """

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
        instructions += GROUNDING_RULES
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
