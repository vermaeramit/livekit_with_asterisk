"""Single source of truth for the system prompt.

The agent and the cache warmer MUST produce a byte-identical prefix - OpenAI
keys its prompt cache on that prefix, so any drift silently creates a second
cache and the warming stops working. Hence one function, imported by both.
"""
from __future__ import annotations

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
    return instructions, kb_mode, kb_tokens
