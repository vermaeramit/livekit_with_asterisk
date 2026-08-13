"""Placeholder substitution and response extraction for campaign tools.

Deliberately free of any livekit import, so BOTH sides can use it: the agent
(agent/tools.py) and the console's test endpoint, which runs in the admin-api
container where livekit.agents is not installed.

That sharing is the whole point. The test button's promise is "this is exactly
what the model would receive" - and the first version of it applied neither the
response path nor the same substitution, so it showed a full document where the
model would have seen one field. A test that does not match reality is worse
than no test, because it is believed.
"""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def fill(template: str | None, args: dict[str, Any]) -> str | None:
    """Substitute {{arg}} from the model's arguments.

    A missing argument becomes empty rather than raising. The model decides what
    it sends, and a half-filled URL that 404s is easier to diagnose from
    tool_invocations than an exception with no record - and in the console it is
    visible immediately, because the resolved URL is shown next to the result.
    """
    if not template or "{{" not in template:
        return template
    return _PLACEHOLDER.sub(lambda m: str(args.get(m.group(1), "")), template)


def extract(body: Any, path: str | None) -> Any:
    """Dotted path into a decoded response, e.g. "service" or "products.0.title".

    Numeric segments index into lists. Anything that misses returns None, which
    the caller renders as the untouched body - a path that silently produced
    nothing would look like an API that returned nothing.
    """
    if not path:
        return body
    cur = body
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur
