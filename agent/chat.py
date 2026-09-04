"""The agent as text: the same brain, without a microphone.

Built so a prompt can be tested without dialling. Every change to a prompt, a
knowledge base or a tool has until now needed a real phone call to see the
effect, and a call takes two minutes and tells you what the agent SAID without
telling you why.

WHAT MAKES THIS A TEST RATHER THAN A LOOKALIKE

It runs the campaign's own configuration through the same code:

  prompt.build_instructions   the same function the call uses, so the knowledge
                              base index and grounding rules are byte-identical
  kb.search                   the same retrieval, the same thresholds
  tools.build_raw             the same HTTP calls, timeouts, normalisation and
                              error strings the model is shown

A second implementation of any of those would drift, and a tester that drifts
is worse than no tester: it gives an answer nobody checks against reality.

WHAT IS DELIBERATELY DIFFERENT

Speech. There is no STT to mishear a bike model, no TTS to mispronounce a
price, no endpointing, no barge-in. So this proves the prompt, the knowledge
and the tools - and proves nothing at all about latency or about what the
caller was actually understood to have said.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field

from openai import AsyncOpenAI

import kb
import prompt as prompt_mod
import store
import tools as tools_mod

log = logging.getLogger("chat")

# Bolted on after the campaign's own instructions rather than replacing them.
# One prompt to maintain, because two would be two that disagree - and the
# knowledge base rules, the grounding rules and the persona are all in the
# campaign's.
#
# Everything here is about the CHANNEL, not about the business.
CHAT_RULES = """

YOU ARE IN A TEXT CHAT, NOT ON A PHONE CALL:
- The person is reading, not listening. Write plainly. Two or three sentences.
- Never write a control marker of any kind. There is no call to end and no line
  to transfer; markers are for the phone and would appear on screen here.
- Do not describe sounds, pauses or hold music.
- Numbers, prices and phone numbers may be written as digits.
- If you would have handed the call to a colleague, say that a person will call
  them back, and ask for a name and a phone number if you do not already have
  them. Do not promise a time you have not been given.
"""

# What the KB search looks like to the model. Same name as the call, so a
# prompt that mentions it by name is talking about the same thing.
_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": ("Look something up in the knowledge base. Use it "
                        "whenever the answer is not already in front of you."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for, in the caller's own words."},
            },
            "required": ["query"],
        },
    },
}

# A runaway tool loop on a phone call is bounded by the caller hanging up.
# Here nothing bounds it, so this does.
MAX_TOOL_ROUNDS = 6


@dataclass
class Step:
    """One thing the agent did on the way to its answer, for the tester."""
    kind: str                       # kb | tool
    name: str
    args: dict = field(default_factory=dict)
    result: str = ""
    ms: int = 0
    # KB only: what was retrieved and how well it matched.
    hits: list = field(default_factory=list)


@dataclass
class Reply:
    text: str
    steps: list[Step] = field(default_factory=list)
    # How long before the model said its first word, as opposed to its last.
    # On a call this is what the caller waits through; the total is not.
    first_token_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    ms: int = 0


async def reply(cfg, history: list[dict], api_key: str,
                tool_specs: list[dict] | None = None, emit=None) -> Reply:
    """One assistant turn, streamed.

    `history` is [{"role": "user"|"assistant", "content": str}, ...] and is
    held by the caller. Nothing is stored here: the tester is a scratchpad, and
    a test conversation should not appear in the call list beside real ones.

    `emit` is awaited with each event as it happens - text deltas, and each
    tool the moment it finishes. Streaming is not decoration: it separates the
    time spent waiting on a tool from the time spent waiting on the model, and
    that separation is the whole question on a real call. A single 6036 ms
    number says nothing about which half to fix.
    """
    started = time.perf_counter()
    emit = emit or _noop
    instructions, _, _ = await prompt_mod.build_instructions(cfg)
    instructions += CHAT_RULES

    client = AsyncOpenAI(api_key=api_key)
    steps: list[Step] = []
    first_token_ms = 0

    # Recording is a no-op: this is not a call, so there is no call row for a
    # tool invocation to belong to. The tool code takes the callback rather
    # than importing the database precisely so this is possible.
    async def _record(**_):
        return None

    runners: dict[str, object] = {}
    schemas: list[dict] = []
    for spec in tool_specs or []:
        try:
            name, schema, run = tools_mod.build_raw(spec, None, _record)
        except Exception:
            log.exception("tool %r could not be built", spec.get("name"))
            continue
        runners[name] = run
        schemas.append({"type": "function",
                        "function": {"name": name, **schema}})

    if cfg.kb_enabled:
        schemas.append(_KB_TOOL)

    messages = [{"role": "system", "content": instructions}] + list(history)
    usage = {"prompt": 0, "completion": 0, "cached": 0}
    answer = ""

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            stream = await client.chat.completions.create(
                model=cfg.llm_model,
                temperature=cfg.llm_temperature,
                messages=messages,
                tools=schemas or None,
                stream=True,
                # Without this the usage block never arrives on a streamed
                # response, and the tester would show a cost of zero.
                stream_options={"include_usage": True},
            )

            text = ""
            # Tool calls arrive in pieces: the name in one delta, the arguments
            # a character at a time across many. Keyed by index because that is
            # the only field present on every fragment.
            pending: dict[int, dict] = {}

            async for chunk in stream:
                if chunk.usage:
                    usage["prompt"] += chunk.usage.prompt_tokens or 0
                    usage["completion"] += chunk.usage.completion_tokens or 0
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    usage["cached"] += getattr(details, "cached_tokens", 0) or 0
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    if not first_token_ms:
                        # The number that matters on a call: how long before it
                        # started SPEAKING, not how long until it finished.
                        first_token_ms = int((time.perf_counter() - started) * 1000)
                        await emit(stage="first_token", ms=first_token_ms)
                    text += delta.content
                    await emit(stage="delta", text=delta.content)

                for tc in delta.tool_calls or []:
                    slot = pending.setdefault(
                        tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments

            if not pending:
                answer = _strip_markers(text, cfg)
                break

            # Put the assistant's own turn back, tool calls and all, or the
            # model has no memory of having asked.
            messages.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["args"]}}
                    for c in pending.values()],
            })

            for call in pending.values():
                out, step = await _run_tool(call["name"], call["args"], cfg,
                                            runners, api_key)
                steps.append(step)
                await emit(stage="step", step=dataclasses.asdict(step))
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": out})
        else:
            # Out of rounds. Said rather than silently truncated - a prompt
            # that loops here would loop on a real call too, and that is the
            # finding.
            answer = ("(the agent kept calling tools and never answered - "
                      f"stopped after {MAX_TOOL_ROUNDS} rounds)")

        return Reply(text=answer, steps=steps,
                     prompt_tokens=usage["prompt"],
                     completion_tokens=usage["completion"],
                     cached_tokens=usage["cached"],
                     first_token_ms=first_token_ms,
                     ms=int((time.perf_counter() - started) * 1000))
    finally:
        await client.close()


def opening(cfg) -> str:
    """What the agent says first, exactly as a call would say it.

    A real call opens with the greeting and the caller answers it. A tester
    that starts on an empty screen makes its first turn structurally different
    from every call - the model has not introduced itself, and the caller's
    first line is a reply to nothing.

    Rendered through the same substitution a call uses, with NO dialler
    context, so placeholders fall back to the defaults after the pipe. That is
    honest rather than convenient: it is what a caller hears when the dialler
    sends nothing, which happens.
    """
    return prompt_mod.render_spoken(cfg.greeting, {}) or ""


async def _noop(**_):
    return None


async def _run_tool(name: str, raw_args: str, cfg, runners,
                    api_key) -> tuple[str, Step]:
    t0 = time.perf_counter()
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        # Streamed arguments are assembled from fragments, so a truncated
        # stream lands here. Empty rather than a crash: the tool then fails
        # with its own message and the step shows what arrived.
        args = {}

    if name == "search_knowledge_base":
        hits = await kb.search(args.get("query", ""), config_name=cfg.name,
                               top_k=cfg.kb_top_k, min_score=cfg.kb_min_score,
                               api_key=api_key)
        out = "\n\n".join(h["content"] for h in hits) or "Nothing found."
        # kb.search returns doc_id and not a name, which is enough for the
        # agent and useless to a person: "which document said that" is the
        # question the tester exists to answer.
        titles = await _doc_titles({h["doc_id"] for h in hits})
        return out, Step(
            kind="kb", name=name, args=args, result=out[:600],
            ms=int((time.perf_counter() - t0) * 1000),
            hits=[{"document": titles.get(h["doc_id"], f"#{h['doc_id']}"),
                   "score": round(h.get("score", 0), 3),
                   "heading": h.get("heading"),
                   "matched": h.get("src")} for h in hits])

    run = runners.get(name)
    if run is None:
        return "That tool is not available.", Step(
            kind="tool", name=name, args=args, result="not available",
            ms=0)

    try:
        # ONE dict, not (ctx, **kwargs). livekit is given the function with a
        # raw_schema, so it hands the arguments over unsplatted and `run`
        # normalises them itself - which is where the digits-only handling for
        # pin codes lives.
        out = await run(args)
    except tools_mod.ToolError as e:
        # A tool failure the model is MEANT to read - "no dealer for that PIN
        # code", not a stack trace. The wording of several of these was chosen
        # on real calls, so it is passed through unchanged.
        out = str(e)
    except Exception as e:
        log.exception("tool %s failed in chat", name)
        out = f"The lookup failed: {type(e).__name__}"
    return str(out), Step(kind="tool", name=name, args=args,
                          result=str(out)[:600],
                          ms=int((time.perf_counter() - t0) * 1000))


async def _doc_titles(doc_ids: set) -> dict:
    if not doc_ids:
        return {}
    rows = await (await store.pool()).fetch(
        "SELECT id, coalesce(title, filename) AS name FROM kb_documents "
        " WHERE id = ANY($1::bigint[])", list(doc_ids))
    return {r["id"]: r["name"] for r in rows}


def _strip_markers(text: str, cfg) -> str:
    """Take out anything the prompt uses to signal the phone.

    The chat rules ask the model not to write them, and models do it anyway -
    asked for [TRANSFER] one wrote [Transfer]. On a call the tts node filters
    these; here they would simply appear on screen.
    """
    for marker in (getattr(cfg, "transfer_marker", None),
                   getattr(cfg, "end_call_marker", None)):
        m = (marker or "").strip()
        if not m:
            continue
        # Case-insensitive for the same reason the call's filter is.
        low = text.lower()
        target = m.lower()
        while target in low:
            i = low.index(target)
            text = text[:i] + text[i + len(m):]
            low = text.lower()
    return text.strip()
