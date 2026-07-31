"""Measure real TTFT per model from THIS server, with a realistic voice-agent prompt."""
import asyncio, os, time
from openai import AsyncOpenAI

SYS = ("You are a helpful voice assistant on a phone call. Keep replies short - "
       "one or two sentences. This is speech, not text. Never use bullet points, "
       "markdown, or emoji. Reply in the same language and script the caller uses.")
USER = "मुझे मेरे कोटक बैंक का बैलेंस चेक करना है"
MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"]
RUNS = 5


async def ttft(client, model):
    t0 = time.perf_counter()
    stream = await client.chat.completions.create(
        model=model, temperature=0.6, stream=True,
        messages=[{"role": "system", "content": SYS},
                  {"role": "user", "content": USER}])
    first = None
    async for ch in stream:
        if first is None and ch.choices and ch.choices[0].delta.content:
            first = (time.perf_counter() - t0) * 1000
    return first


async def main():
    c = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"{'model':16} {'min':>8} {'median':>8} {'max':>8}")
    for m in MODELS:
        vals = []
        for _ in range(RUNS):
            try:
                v = await ttft(c, m)
                if v:
                    vals.append(v)
            except Exception as e:
                print(f"{m:16} ERROR: {type(e).__name__}: {e}")
                break
        if vals:
            vals.sort()
            print(f"{m:16} {vals[0]:7.0f}ms {vals[len(vals)//2]:7.0f}ms {vals[-1]:7.0f}ms")


asyncio.run(main())
