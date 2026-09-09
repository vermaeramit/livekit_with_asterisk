# Feature flowchart

Paste the block below into **[mermaid.live](https://mermaid.live)** and use
*Actions → PNG* or *SVG*. The text comes out perfect because it is text, not a
picture of text — which a flowchart with this many labels will never be from an
image model.

```mermaid
flowchart TD
    START([Call from the dialler]):::entry
    START --> LIMIT

    LIMIT{Campaign<br/>at its limit?}:::gate
    LIMIT -->|"yes"| HOLD["<b>Queue</b><br/>hold message plays<br/>no AI cost while waiting"]:::feat
    HOLD -.->|"a slot frees"| LIMIT
    LIMIT -->|"no"| ANSWER

    ANSWER["<b>Agent answers</b><br/>greeting plays from cache"]:::core
    ANSWER --> LISTEN

    LISTEN["<b>Listens</b><br/>speech to text<br/>11 Indian languages<br/>barge-in — interrupt any time"]:::feat
    LISTEN --> THINK

    THINK{"<b>Understands</b><br/>what does it need?"}:::gate
    THINK -->|"a fact"| KB["<b>Knowledge base</b><br/>PDF · Word · web page<br/>answers from your documents"]:::feat
    THINK -->|"an action"| API["<b>Your own APIs</b><br/>called mid-conversation"]:::feat
    THINK -->|"a person"| HUMAN["<b>Human handover</b><br/>confirmed with the caller<br/>inside working hours"]:::feat
    THINK -->|"nothing"| SPEAK

    KB --> SPEAK
    API --> SPEAK

    SPEAK["<b>Speaks</b><br/>text to speech<br/>natural Indic voices"]:::core
    SPEAK --> MORE

    MORE{Conversation<br/>finished?}:::gate
    MORE -->|"no"| LISTEN
    MORE -->|"yes"| DONE

    HUMAN --> TRANSFERRED([Connected to a colleague]):::exit
    DONE([Call ends]):::exit

    DONE --> RECORD
    TRANSFERRED --> RECORD

    RECORD["<b>Every call kept</b><br/>recording · transcript<br/>timing of every turn"]:::feat
    RECORD --> POST["<b>Sent onward</b><br/>posted to your system<br/>with retries"]:::feat
    RECORD --> INSIGHT["<b>Analytics</b><br/>volume · response times<br/>outcomes · spend"]:::feat
    RECORD --> GAPS["<b>Knowledge gaps</b><br/>what callers asked<br/>and nothing answered"]:::feat

    subgraph CONSOLE ["Web console — everything below is set per campaign, no deploy"]
        direction LR
        C1["Prompt<br/>& voice"]:::cfg
        C2["Knowledge<br/>base"]:::cfg
        C3["Tools<br/>& routing"]:::cfg
        C4["Limits<br/>& handover"]:::cfg
        C5["Provider<br/>keys"]:::cfg
        C6["Live monitor<br/>& alerts"]:::cfg
    end

    CONSOLE -.->|"configures"| ANSWER

    WIDGET(["<b>Website chat widget</b><br/>same brain, same documents"]):::alt
    WIDGET -.-> THINK

    classDef entry fill:#4f46e5,stroke:#4338ca,color:#ffffff,stroke-width:2px
    classDef exit fill:#0f766e,stroke:#0d5d56,color:#ffffff,stroke-width:2px
    classDef core fill:#eef2ff,stroke:#6366f1,color:#1e1b4b,stroke-width:2px
    classDef feat fill:#ffffff,stroke:#c7d2fe,color:#312e81,stroke-width:1.5px
    classDef gate fill:#fff7ed,stroke:#fb923c,color:#7c2d12,stroke-width:1.5px
    classDef cfg fill:#f8fafc,stroke:#cbd5e1,color:#334155,stroke-width:1px
    classDef alt fill:#fdf2f8,stroke:#f472b6,color:#831843,stroke-width:1.5px
```

---

## The same thing, wide

For a 16:9 slide, where tall does not fit.

```mermaid
flowchart LR
    START([Call arrives]):::entry --> LISTEN

    LISTEN["<b>Listens</b><br/>11 languages<br/>barge-in"]:::feat --> THINK

    THINK{"<b>Understands</b>"}:::gate
    THINK -->|"a fact"| KB["<b>Knowledge base</b><br/>your documents"]:::feat
    THINK -->|"an action"| API["<b>Your APIs</b><br/>mid-call"]:::feat
    THINK -->|"a person"| HUMAN["<b>Handover</b><br/>to a colleague"]:::feat

    KB --> SPEAK
    API --> SPEAK
    THINK -->|"nothing"| SPEAK

    SPEAK["<b>Speaks</b><br/>natural Indic voice"]:::core --> LOOP{More?}:::gate
    LOOP -->|"yes"| LISTEN
    LOOP -->|"no"| OUT

    OUT["<b>Recording · transcript<br/>analytics · posted onward</b>"]:::feat
    HUMAN --> OUT

    classDef entry fill:#4f46e5,stroke:#4338ca,color:#ffffff,stroke-width:2px
    classDef core fill:#eef2ff,stroke:#6366f1,color:#1e1b4b,stroke-width:2px
    classDef feat fill:#ffffff,stroke:#c7d2fe,color:#312e81,stroke-width:1.5px
    classDef gate fill:#fff7ed,stroke:#fb923c,color:#7c2d12,stroke-width:1.5px
```

---

## If it has to come from an image model

It will look good and the labels will be wrong. Generate it, then correct every
word in PowerPoint or Figma — or use the Mermaid above and skip that step.

```
Create a clean, modern vertical flowchart illustration for a product slide.
16:9, flat vector, on a very light grey background, with soft shadows and a
restrained palette of white, light indigo and one warm orange accent.

A single flow runs from top to bottom, connected by thin arrows with small
arrowheads:

1. a rounded pill shape at the top, filled solid indigo
2. an orange-outlined diamond, with one arrow leaving its right side into a
   white card, and that card looping back to the diamond
3. a white rounded card
4. a second orange-outlined diamond, with three arrows fanning out to the right
   into three separate white cards stacked vertically, all three rejoining
   the flow below
5. a white rounded card
6. a third orange-outlined diamond, one arrow looping back upward, one
   continuing down
7. a rounded pill shape at the bottom, filled solid teal, with three small
   white cards fanning out beneath it

Every card carries one simple line icon in indigo in its top-left corner:
a soundwave, an open book, two interlocking gears, two human figures, a
circle with a soundwave, a bar chart, a bell.

TEXT — no text anywhere in the image. No letters, no numbers, no labels, no
title, no logo, no watermark. Leave the cards empty apart from their icons, so
that words can be placed on top afterwards.
```
