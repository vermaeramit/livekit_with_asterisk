# Prompts for generating diagrams of this system

Paste one of these into ChatGPT (or any image model) to get a picture of what
this platform is and how a call moves through it.

---

## Read this first — what image models can and cannot do

**They cannot spell reliably.** Any picture with more than about eight words in
it will come back with garbled labels: "Knowlege Bse", "Astersik", numbers that
drift. This is not a prompt problem and no amount of "make sure the text is
correct" fixes it.

So the prompts are ordered by how much text they need:

| Prompt | Words in the image | Use it for |
|---|---|---|
| **1 — Call journey** | ~10 | A slide showing how a call flows |
| **2 — What it does** | ~14 | A capability overview |
| **3 — Architecture** | ~25 | A technical slide — expect to fix the labels |
| **4 — Cover image** | 0 | A title slide or a header |

**If the labels have to be exactly right, do not use an image model.** Use the
Mermaid diagram at the bottom of this file instead: paste it into
mermaid.live, or into any Markdown that renders Mermaid, and export the SVG.
The text will be perfect because it is text, not a picture of text.

A good working method: generate the picture from prompt 1 or 4, and put the
real words on top of it in PowerPoint or Figma.

**Replace the name.** These say "Voice Console". Put the product's real name in
before generating, or leave the name out entirely and add it yourself.

---

## Prompt 1 — The call journey

For explaining, in one picture, what happens when a call comes in. The fewest
words, so the best chance of a clean result.

```
Create a clean, modern horizontal flow diagram for a business slide, 16:9,
on a very light grey background.

Six rounded rectangular cards in a single row, connected left to right by thin
arrows. Each card has a simple line icon above a one or two word label. Use a
restrained palette: white cards, soft grey borders, a single indigo accent for
the icons and arrows. Flat vector illustration style, generous white space,
subtle drop shadows. No gradients, no 3D, no clutter.

The six cards, in order:
1. icon: a telephone — label: "Call"
2. icon: a soundwave — label: "Listen"
3. icon: a document with a magnifier — label: "Look up"
4. icon: a speech bubble — label: "Answer"
5. icon: two people — label: "Handover"
6. icon: a bar chart — label: "Report"

Only those six words appear in the image. No other text, no title, no legend,
no watermark. Keep every label short and spelled exactly as written.
```

---

## Prompt 2 — What it does

A capability overview, for a page that says "here is what the platform gives
you" without describing the plumbing.

```
Create a modern flat-vector infographic for a product slide, 16:9, light
background, arranged as a 3x2 grid of six square cards with rounded corners.

Each card holds one large simple line icon in indigo, with a short label
underneath in dark grey. White cards, soft shadow, plenty of space between
them. Corporate but friendly. No gradients, no photographs, no 3D.

The six cards:
1. a microphone with soundwaves — "Real-time voice"
2. an open book — "Knowledge base"
3. a globe with speech marks — "Indian languages"
4. two arrows between a robot and a person — "Human handover"
5. a sliding control panel — "Per campaign"
6. a line graph going up — "Live analytics"

Only those twelve words appear. No headings, no paragraphs, no watermark,
no logo. Spell each label exactly as written.
```

---

## Prompt 3 — The architecture

More text than an image model handles well. Generate it, then expect to correct
the labels by hand — or use the Mermaid version below instead.

```
Create a clean technical architecture diagram for an engineering slide, 16:9,
white background, flat vector style with thin lines and a restrained palette
of white, grey and one indigo accent.

Three horizontal bands stacked vertically, each a rounded rectangle with a thin
border and a short label on its left edge.

TOP BAND, labelled "Dialler": a single box, "Outbound calls".
An arrow points down from it into the middle band.

MIDDLE BAND, labelled "Our server": four boxes in a row, connected by short
arrows — "Telephony", "Media server", "AI agent", "Console". Below this row,
one wide box spanning underneath them, labelled "Database".

BOTTOM BAND, labelled "Cloud": three small boxes in a row — "Speech", "Language
model", "Voice". A dashed arrow points up from this band to the "AI agent" box.

Simple line icons may sit above each box label. No other text anywhere in the
image. Keep the words short and spelled exactly as written above.
```

---

## Prompt 4 — Cover image, no text at all

The safest thing an image model can make, because there is nothing to misspell.
Good for a title slide with your own text on top.

```
Create a wide 16:9 abstract header illustration for a technology product,
flat vector style, on an off-white background.

A stylised soundwave flows from left to right across the frame and gradually
transforms into a smooth data line that rises gently. A simple telephone
handset outline sits at the far left, and a small abstract network of connected
dots sits at the far right.

Indigo and soft grey only, with a lot of empty space in the upper half so text
can be placed there later. Minimal, calm, corporate. No text, no letters, no
numbers, no logo, no watermark anywhere in the image.
```

---

## The words, if you want to label a picture yourself

Written out so a slide can be captioned accurately, whatever the picture ends
up looking like.

**What it is, in one sentence**

> An AI voice agent that answers calls in Hindi and other Indian languages,
> looks up answers in the customer's own documents, calls their own systems
> mid-conversation, and hands over to a person when it should.

**How a call actually moves**

1. The customer's existing dialler places the call and waits for a person to
   answer.
2. The connected call is handed to this platform, along with who the caller is
   and what they own.
3. The campaign's limit is checked. Over it, the caller hears a hold message and
   is connected the moment a slot frees — no AI is spent while waiting.
4. The agent joins and starts talking. Voice detection and turn detection run on
   the server itself, so the caller can interrupt mid-sentence.
5. Speech becomes text, the language model answers, the answer becomes speech —
   all streaming, all overlapping.
6. Product questions are looked up in the knowledge base rather than answered
   from memory.
7. The customer's own APIs are called mid-call when the conversation needs them.
8. The caller can ask for a person and be transferred, or the agent ends the
   call itself when the conversation is done.
9. The recording, the transcript and every timing are stored, and the finished
   call is posted to the customer's system.

**What the console does**

- Calls list, and every call with its transcript, recording, per-turn timings
  and which documents were used
- Dashboard — volume, response times, outcomes, token and character usage
- Live monitor of calls in progress
- Alerts on provider failures, latency, and calls that stop arriving
- Knowledge gaps — the questions callers asked that nothing answered
- Per campaign: prompt, voice and model, knowledge base, tools, routing, keys,
  limits and handoff, postback, a chat tester, and prompt history
- Multi-client: separate clients, users and roles, with provider keys held per
  client and encrypted
- An embeddable chat widget for a website, on the same campaign and knowledge base

**Numbers, all measured on this deployment — check them before publishing**

- ~0.75 s of ring before the agent is ready
- ~2.4 s from the call arriving to the first spoken word
- 20 concurrent calls run clean
- 11 Indian languages
- Media never leaves the local network; only AI requests go out

---

## The accurate version — Mermaid, not an image model

For anything where the labels have to be right. Paste into
[mermaid.live](https://mermaid.live) and export SVG or PNG.

```mermaid
flowchart TD
    D["Customer's dialler<br/>places the call"] -->|"connected call<br/>+ caller details"| A

    subgraph OURS["Our server — media never leaves the network"]
        A["Telephony<br/>Asterisk"] --> Q{"Campaign<br/>at its limit?"}
        Q -->|"yes"| H["Hold message<br/>no AI is spent"]
        H -.->|"a slot frees"| Q
        Q -->|"no"| M["Media server<br/>LiveKit"]
        M --> AG["AI agent"]

        AG --- V["Voice + turn detection<br/>on this server"]
        AG --> KB[("Knowledge base")]
        AG --> DB[("Calls, transcripts,<br/>configuration")]
        C["Web console"] --> DB
    end

    AG -->|"streaming"| P["Speech · Language model · Voice"]
    AG -->|"mid-call"| T["Customer's own APIs"]
    AG -->|"on request"| HU["A human colleague"]
    DB -->|"when the call ends"| PB["Posted to the<br/>customer's system"]
```

Same thing as a single left-to-right journey, for a wider slide:

```mermaid
flowchart LR
    A["Call arrives"] --> B["Caller speaks"]
    B --> C["Understood"]
    C --> D{"Needs a fact?"}
    D -->|"yes"| E[("Knowledge base")]
    D -->|"needs an action"| F["Customer's API"]
    E --> G["Answer spoken back"]
    F --> G
    D -->|"no"| G
    G --> H{"Wants a person?"}
    H -->|"yes"| I["Handed to a human"]
    H -->|"no"| B
    G --> J["Recorded, timed,<br/>posted onward"]
```
