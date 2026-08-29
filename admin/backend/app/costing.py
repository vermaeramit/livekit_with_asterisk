"""What a call cost, from what it used and what those units are priced at.

One module because the arithmetic has three places to go wrong and none of them
announce themselves:

  * `llm_prompt_tokens` ALREADY INCLUDES the cached ones. Charging both at the
    input rate is the difference between 3,06,984 tokens and 14,376 on a real
    call - roughly twenty times the true figure, in a number people trust.
  * Providers quote per million tokens and per audio hour; we store seconds and
    counts. The conversion belongs next to the prices it applies to.
  * A missing rate is not a zero. A call with no price for its model costs an
    unknown amount, and saying so is the only honest answer - a confident 0.00
    reads as "free".

Prices are read at display time rather than stamped onto the call. That means
correcting a rate corrects every call it was ever wrong for, which is what you
want while the table is being filled in. It also means a call is priced at
today's rate rather than the day's - worth knowing before this becomes an
invoice.
"""
from __future__ import annotations

from decimal import Decimal

# usage field -> (rate kind, which provider column, which model column)
#
# Named rather than inferred. The mapping between "what we counted" and "what it
# is charged as" is exactly the sort of thing that reads as obvious and is not:
# tts_characters and tts_audio_seconds are both TTS usage, and a provider
# charges for one of them, not both.
_LEGS: tuple[tuple[str, str, str, str], ...] = (
    ("llm", "llm_input", "llm_provider_used", "llm_model_used"),
    ("llm", "llm_cached", "llm_provider_used", "llm_model_used"),
    ("llm", "llm_output", "llm_provider_used", "llm_model_used"),
    ("tts", "tts_characters", "tts_provider_used", "tts_model_used"),
    ("tts", "tts_seconds", "tts_provider_used", "tts_model_used"),
    ("stt", "stt_seconds", "stt_provider_used", "stt_model_used"),
)

# How many of a unit make up one priced unit.
_PER = {
    "per_million": Decimal(1_000_000),
    "per_hour": Decimal(3600),
    "per_minute": Decimal(60),
    "per_unit": Decimal(1),
}


def _first_provider(v: str | None) -> str | None:
    """The primary leg. "sarvam,openai" means a fallback fired mid-call.

    Priced as the primary, and flagged rather than split: we know both were
    used and not how much of the call each served, and inventing a division
    would be worse than saying the figure is approximate.
    """
    if not v:
        return None
    return v.split(",")[0].strip() or None


def quantities(call: dict) -> dict[str, Decimal]:
    """How much of each priced thing the call used."""
    prompt = Decimal(call.get("llm_prompt_tokens") or 0)
    cached = Decimal(call.get("llm_prompt_cached_tokens") or 0)
    # Never below zero. Providers have reported cached > prompt on a retry, and
    # a negative quantity here would quietly subtract from the bill.
    return {
        "llm_input": max(prompt - cached, Decimal(0)),
        "llm_cached": cached,
        "llm_output": Decimal(call.get("llm_completion_tokens") or 0),
        "tts_characters": Decimal(call.get("tts_characters") or 0),
        "tts_seconds": Decimal(str(call.get("tts_audio_seconds") or 0)),
        "stt_seconds": Decimal(str(call.get("stt_audio_seconds") or 0)),
    }


def pick_rate(rates: list[dict], provider: str | None, model: str | None,
              kind: str) -> dict | None:
    """The rate that applies, model-specific first.

    A row naming the model beats one that does not, so a campaign on gpt-4.1 is
    not priced at gpt-4.1-mini's rate just because someone set a provider-wide
    default first.
    """
    if not provider:
        return None
    exact = None
    generic = None
    for r in rates:
        if r["provider"] != provider or r["kind"] != kind:
            continue
        if r["model"] and model and r["model"] == model:
            exact = r
        elif not r["model"]:
            generic = r
    return exact or generic


def price_call(call: dict, rates: list[dict], usd_to_inr: Decimal | None) -> dict:
    """-> the cost of one call, and what could not be priced.

    Never raises and never guesses. Legs with no rate are listed by name so the
    console can say which row to go and add, rather than showing a total that
    is silently short.
    """
    qty = quantities(call)
    legs: dict[str, Decimal] = {"llm": Decimal(0), "tts": Decimal(0),
                                "stt": Decimal(0)}
    unpriced: dict[str, list[str]] = {"llm": [], "tts": [], "stt": []}
    priced_layers: set[str] = set()

    for layer, kind, provider_col, model_col in _LEGS:
        amount = qty.get(kind, Decimal(0))
        if amount <= 0:
            continue
        provider = _first_provider(call.get(provider_col))
        rate = pick_rate(rates, provider, call.get(model_col), kind)
        if rate is None:
            unpriced[layer].append(f"{provider or 'unknown'} · {kind}")
            continue
        per = _PER.get(rate["unit"], Decimal(1))
        legs[layer] += (amount / per) * Decimal(str(rate["usd_price"]))
        priced_layers.add(layer)

    # Only complain about a layer that got NO price at all.
    #
    # A provider charges for TTS by characters or by audio seconds, never both,
    # and we count both. Asking for the one it does not charge for would send
    # somebody to add a row that then doubles every TTS figure on the platform.
    missing = [name for layer, names in unpriced.items()
               if layer not in priced_layers for name in names]

    total = sum(legs.values(), Decimal(0))
    priced_any = bool(priced_layers)
    out = {
        "usd": {k: float(round(v, 6)) for k, v in legs.items()},
        "usd_total": float(round(total, 6)),
        # Named, not counted. "add a rate for soniox · stt_seconds" is a job;
        # "3 rates missing" is a puzzle.
        "missing_rates": sorted(set(missing)),
        # False means every leg was unpriced: there is no figure at all, which
        # the console must show differently from a genuine zero.
        "priced": priced_any,
        # A fallback fired, so the call was partly served by a provider it is
        # not being priced at.
        "approximate": any(
            "," in (call.get(c) or "")
            for c in ("llm_provider_used", "tts_provider_used", "stt_provider_used")
        ),
    }
    if usd_to_inr:
        out["inr"] = {k: float(round(v * usd_to_inr, 4)) for k, v in legs.items()}
        out["inr_total"] = float(round(total * usd_to_inr, 4))
        out["usd_to_inr"] = float(usd_to_inr)
    return out
