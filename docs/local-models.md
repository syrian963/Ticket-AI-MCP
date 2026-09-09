# Composing with a local model

`compose` turns a title into a ticket, and the model that writes it can run on
your own machine: no key, no account, nothing leaving the laptop. This is what
that actually produces, measured rather than described.

Everything below was run against live boards with **llama3.2:3b** — a 2 GB
model, deliberately the small end, because the interesting question is whether
the tool can make a weak model useful rather than whether a strong one needs
help.

## Setup

```bash
ollama pull llama3.2:3b
export TICKET_AI_WRITER=ollama
export TICKET_AI_MODEL=llama3.2:3b
ticket-ai compose --title 'Lager: Etikettendruck bricht bei mehr als zehn Positionen ab'
```

## What came back

Seven titles, three boards, one profile learned per board beforehand.
`alignment` is the share of applicable checks the draft passed, measured
against that board's own tickets.

| Board | Ticket | Alignment | Checks | Attempts | Time |
|---|---|---|---|---|---|
| a private board (GitLab, German) | filter | 100% | 3 | 2 | 51s |
| a private board | catalogue order | 100% | 3 | 2 | 48s |
| a private board | PDF generation | 100% | 2 | 2 | 43s |
| pydantic (GitHub, English) | field alias | 72% | 8 | 2 | 70s |
| pydantic | discriminated union | 83% | 8 | 2 | 71s |
| astral-sh/uv (GitHub, English) | lock vs sync | 80% | 5 | 2 | 41s |
| astral-sh/uv | editable install | 80% | 5 | 2 | 27s |

A run costs 30–70 seconds on a laptop CPU, and always takes the revision pass —
the first draft has never yet cleared the corpus on the first try with a model
this size.

Note the **Checks** column. pydantic's issue form is strict, so eight checks
apply and 72% means missing two of them; a private board has no board-wide
template, so three checks apply and 100% is a weaker claim. Alignment is only
comparable within a board.

Every remaining finding on the English runs was "no labels" or "no checklist" —
the labels one because the batch did not pass `--label`, which is a gap in how
it was run rather than in what was written.

## A German sample, at 100%

The boards above are real and two of them are public, but printing a generated
ticket from a private one would be publishing someone's product detail. So this
sample comes from a corpus of invented tickets for a fictional warehouse tool -
a real model run, against a board that belongs to nobody.

Title in, nothing else:

    Lager: Etikettendruck bricht bei mehr als zehn Positionen ab

Out, after one revision, 49 seconds, 100% over 7 checks:

```markdown
### Ziel
Das Etikettendruck-System bricht bei mehr als zehn Positionen ab, wodurch die
korrekte Darstellung der Etiketten auf den Produkten nicht möglich ist.

### Abnahme
- [ ] Die korrekte Anzahl von Etiketten wird immer angezeigt.
- [ ] Bei mehr als zehn Positionen sollte das System eine Warnung anzeigen...

### Details
Das System funktioniert nur bis zu zehn Positionen und zeigt danach eine
falsche Anzahl von Etiketten.
```

Correct German, the corpus's own sections, its own length, its own checkbox
habit. It is also repetitive - the second criterion runs to fifty words and
says one thing three ways - which is what a 3B model does, and no amount of
measurement fixes it.

## Two bugs this found

Neither showed up on a single ticket. Both needed a batch.

**The model invented a `Labels` section.** The prompt listed the project's
labels without saying what they were for, so several drafts came back with
labels written into the body. Labels are tracker metadata. The system prompt
now says so, and it went from four runs in seven to one.

**Feedback leaked into the ticket.** One draft contained the sentence *"Anyone
will know it did when they see..."* - which is the wording of a finding's
`fix`, not anything about the bug. The revision prompt handed the model a draft
and a list of corrections in one flat block, and a 3B model could not tell
which was the document. The draft is now fenced and the instructions say
outright that the feedback is not text to reuse. Zero leaks in seven runs
afterwards.

The German runs went from 67% to 100% on the back of those two fixes.

## What it does not do well

- **It invents causes.** One uv draft explained the bug as "uv lock and uv sync
  are using different versions of the uv library", which is made up. The system
  prompt forbids exactly this and a 3B model does it anyway. Read what comes
  back before posting it.
- **It writes short.** Before the prompt was tightened, every draft came in at
  350–500 characters against corpus medians near 1000. It is better now and
  still the most common finding.
- **The revision pass is one round.** A second fixes mechanical misses; a third
  mostly rewords.

None of this is a reason not to use a small model. It is the reason the draft
is measured afterwards, and the reason `--fail-under` exists.

## Where a bigger model helps

Quality of prose, not compliance with the template - the template is handled by
the measurements either way. If the tickets are read by people outside the
team, a 7B or larger model is worth the disk. `qwen2.5:7b` is noticeably better
at German.

The same command works against any OpenAI-compatible endpoint:

```bash
export TICKET_AI_WRITER=openai
export TICKET_AI_BASE_URL=https://openrouter.ai/api/v1
export TICKET_AI_MODEL=...
export TICKET_AI_API_KEY=...
```

## If the tickets should not leave the building

That is the case `--writer ollama` is for. The model runs locally, and the only
network traffic is the tracker API this tool already needs.

## A note for WSL users

Ollama installed as a Windows application binds to `127.0.0.1` on Windows,
where a WSL shell cannot reach it. Either run `ticket-ai` from Windows, or set
`OLLAMA_HOST=0.0.0.0` in the Ollama app's settings - which also exposes it to
your local network, so decide whether you want that.
