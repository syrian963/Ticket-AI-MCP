# Five tickets it produced

Five titles went in. These five bodies came back, printed unedited, each with
the score `review_draft` gave it against that board's own tickets.

The model is the assistant this ran inside — the MCP path, where the model is
already there and no key is involved. The board is a corpus of twenty-one
tickets for a warehouse tool that exists only in this file, so the samples can
be printed in full without publishing anyone's product.

| | Alignment | Checks | Length |
|---|---|---|---|
| Etikettendruck bricht ab | 100% | 7 | 1128 |
| Buchung laesst sich nicht zuruecknehmen | 100% | 7 | 971 |
| Bestandsliste nicht sortierbar | 100% | 7 | 907 |
| Zieldrucker nicht sichtbar | 100% | 7 | 849 |
| Keine Warnung bei abweichender Menge | 100% | 7 | 980 |

The corpus is German, uses `Ziel` and `Abnahme`, runs to a median of 470
characters and puts checkboxes under the criteria. Nobody configured any of
that; it was measured from the twenty-one.

---

## 1. Etikettendruck bricht bei mehr als zehn Positionen ab

```markdown
## Ziel

Als Lagerist moechte ich auch bei mehr als zehn Positionen einen vollstaendigen
Etikettensatz drucken koennen, damit ich den Sammeldruck nicht in Haeppchen
aufteilen und hinterher von Hand zusammensuchen muss.

## Abnahme

- [ ] Ein Sammeldruck ueber 25 Positionen erzeugt 25 Etiketten
- [ ] Bricht der Druck ab, nennt die Meldung die erste nicht gedruckte Position
- [ ] Ein Test deckt die Grenze bei elf Positionen ab

Aktuell bricht der Sammeldruck ab, sobald mehr als zehn Positionen im Auftrag
stehen. Die ersten zehn Etiketten kommen korrekt aus dem Drucker, danach endet
der Vorgang ohne Meldung...

Einzelne Etiketten sind nicht betroffen und werden weiterhin korrekt erzeugt.
Woran die Grenze bei zehn liegt, ist noch nicht untersucht.
```

Note the last sentence. The cause is not known, so the ticket says the cause is
not known. That line is the difference between a report and a guess, and it is
the thing worth insisting on from whatever writes these.

## 2. Buchung im Wareneingang laesst sich nicht zuruecknehmen

```markdown
## Ziel

Als Lagerist moechte ich eine Buchung im Wareneingang zuruecknehmen koennen,
damit ein Zahlendreher nicht bedeutet, dass ich den Bestand von Hand
gegenbuchen muss.

## Abnahme

- [ ] Eine Buchung laesst sich innerhalb der Schicht zuruecknehmen
- [ ] Der Bestand entspricht danach exakt dem Stand vor der Buchung
- [ ] Die Ruecknahme steht mit Zeitstempel in der Buchungshistorie

Heute ist eine Buchung endgueltig... In der Historie stehen anschliessend zwei
Buchungen, von denen keine als Korrektur erkennbar ist. Bei der naechsten
Inventur ist nicht mehr nachvollziehbar, was Zugang war und was Berichtigung.

Ob die Ruecknahme nach Schichtende noch moeglich sein soll, ist offen und
sollte im Refinement geklaert werden.
```

An open question is named as an open question rather than decided quietly.

## 3. Bestandsliste laesst sich nicht nach Menge sortieren

```markdown
## Abnahme

- [ ] Die Liste laesst sich auf- und absteigend nach Menge sortieren
- [ ] Die gewaehlte Sortierung ueberlebt das Oeffnen einer Position und das
      Zurueckgehen zur Liste
- [ ] Die Sortierung ueberlebt das Neuladen der Seite

Sortieren allein reicht nicht: Sobald man eine Position oeffnet und
zurueckgeht, waere eine verlorene Sortierung genauso muehsam wie gar keine.
Deshalb steht das Wiederfinden mit in den Kriterien.
```

The criteria explain why they are the criteria — the half a reviewer would
otherwise have to ask about.

## 4. Zieldrucker ist beim Etikettendruck nicht sichtbar

```markdown
Im Lager stehen zwei Drucker, einer am Wareneingang und einer an der Packbank.
Der Druck geht immer an den zuletzt eingerichteten, und das steht nirgends. In
der Praxis laeuft jemand zum falschen Geraet, findet nichts, und druckt ein
zweites Mal.
```

The consequence is stated in terms of what someone does, not in terms of a
missing feature. That is what makes the priority arguable.

## 5. Keine Warnung bei stark abweichender Menge im Wareneingang

```markdown
Die Schwelle ist bewusst grob gewaehlt: Eine Warnung bei jeder kleinen
Abweichung wird weggeklickt und schuetzt dann vor gar nichts. Ob Faktor zwei
die richtige Grenze ist, sollte nach ein paar Wochen anhand der tatsaechlichen
Rueckfragen nachgeschaerft werden.
```

A number that was chosen rather than derived says so, and says how it should be
corrected later.

---

## What the tool contributed, and what it did not

It did not write a word of the above. What it did:

- **Said what shape a ticket takes here** — `Ziel` then `Abnahme`, in that
  order, at roughly 470 characters, with checkboxes. All measured from
  twenty-one tickets, none of it configured.
- **Marked each draft before it existed.** Five for five at 100% over seven
  checks, each check citing a count.

The order matters more than it sounds. Sections were sorted by frequency at
first, which put the criteria above the goal — and a model filling that in
wrote each section's content under the other's heading. Ordering by where
headings actually sit inside tickets fixed it.

## The step that does not show in the output

Four subjects were written and then thrown away.

Before writing anything about pydantic, three claims were checked against
pydantic 2.13.5 — that `extra="forbid"` is not inherited by a generic subclass,
that error locations are wrong inside nested sequences, and that a field alias
is ignored when validating from attributes. All three were false. pydantic
handles all three correctly, so no ticket was written.

That is not a limitation of the tool; the tool has no opinion about whether a
bug is real. It is the part of ticket-writing that stays a person's job, or the
job of a model willing to run the code before describing it. A ticket that
scores 100% and reports a bug that does not exist is worse than no ticket, and
nothing here can tell the difference.

Which is why `alignment` is reported as alignment and never as quality.

## Reproducing this

```bash
export TICKET_AI_TRACKER=github
export TICKET_AI_PROJECT=your-org/your-repo

ticket-ai learn --sample 60
ticket-ai style                                  # the shape it found
ticket-ai context 'the subject you are about to write about'
ticket-ai draft --title '...' --file draft.md    # the score, before it exists
```

Over MCP the same three steps are `ticket_template`, `ticket_context` and
`review_draft`, and the assistant does the writing in between.

[docs/local-models.md](local-models.md) covers the other path — a 2 GB model on
your own machine, with measured timings and what it gets right and wrong.
