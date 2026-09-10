# SPDX-License-Identifier: MIT

"""Title in, ticket out - with the model kept on a short leash.

The model writes one thing: German or English prose into a shape this tool
worked out by counting. It is told the sections the team uses and why, the
length they write to, the labels in play, and what already exists on the
subject. It is not asked to invent structure, guess conventions, or decide
whether the ticket is a good idea.

Then the draft goes through `review_draft`, the same measurement any ticket
gets, and if it falls short the findings go back to the model once. That loop
is the reason a small local model is usable here: it is not being trusted, it
is being marked, and the marking cites counts over the team's own tickets.

One revision, not a loop to convergence. A second pass fixes the mechanical
misses - a section left out, half the length; a third mostly rewords, and a
model that cannot satisfy the corpus in two attempts is telling you the corpus
wants something it does not have.

**What comes back is a draft.** Nothing here creates a ticket, and the review
that accompanies it is there so the person reading can see exactly how far
from the house style the machine got.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import Context
from .profile import Profile
from .review import Review
from .review import review_draft as _review_draft
from .textstats import normalise_heading
from .writers.base import Writer

SYSTEM = """You write tickets for one specific team, in their house style.

Rules, in order of importance:

1. Write in {language}. Every sentence you write. The section headings are the
   exception: they are the team's own, copied exactly, even when they are in
   another language - translating one makes it a section this team does not
   have.
2. Use exactly the sections you are given, with those headings, in that order.
   Do not add sections. Do not rename them. If you are told this team writes
   prose, that is the instruction: no headings at all. In particular, labels
   are metadata the tracker stores separately - never write a "Labels"
   section, and never list them in the body.
3. Say only what the input supports. You are given a title, related tickets and
   file paths. If you do not know why something is broken, describe what is
   wrong and what should happen instead - never invent a cause, an error
   message, a version number or a stack trace.
4. Write about {target} characters. Short is the common failure: say what
   happens now, what should happen instead, and how someone would tell the
   difference.
5. Output the description body only. No title, no preamble, no closing remark,
   no note about what you changed, and never any sentence from these
   instructions or from feedback you were given - those are directions to you,
   not text for the ticket.
"""

# One revision. See the module docstring for why not more.
REVISIONS = 1


@dataclass(frozen=True, slots=True)
class Composed:
    """A draft, and how it measured."""

    title: str
    body: str
    review: Review
    attempts: int
    model: str


def _skeleton_block(profile: Profile) -> list[str]:
    """The section list, or an instruction to write without one.

    Two shapes rather than one, because the heading and the content have to
    agree. A board with no recurring section used to get "Sections to use, and
    why each one:" followed by "(no recurring sections)" - a heading promising
    a list, and then no list - while the system prompt was separately telling
    the model to use exactly the sections it was given. Three of the
    forty-three boards in the fleet write pure prose, and a model handed that
    contradiction resolves it the way models do: it invents the sections.
    """
    lines = [f"## {heading}    <- {why}" for heading, why in profile.skeleton()]
    if lines:
        return ["Sections to use, and why each one:", *lines]
    return [
        # Not "no heading recurs": on MongoDB's SERVER board one does, it is
        # simply not common enough for the skeleton to ask for. The sentence
        # has to be true of both that board and one with no headings at all.
        "This team has no section every ticket uses. Write the description as "
        "prose paragraphs, with no headings at all.",
    ]


def build_prompt(title: str, profile: Profile, context: Context | None = None) -> str:
    """Everything the model gets, and nothing it has to guess.

    Assembled from measurements rather than from instructions about ticket
    writing in general. "78% of tickets here have this section" is a fact about
    this team; "tickets should have acceptance criteria" is the advice the
    whole tool exists to replace, and putting it in the prompt would smuggle it
    back in.
    """
    parts = [
        f"Ticket title:\n{title}\n",
        *_skeleton_block(profile),
        "",
    ]

    if profile.checkbox_rate >= 0.5:
        parts.append(
            f"{round(profile.checkbox_rate * 100)}% of tickets here use checkboxes "
            "(- [ ]) for the criteria. Use them."
        )
    if profile.common_labels:
        parts.append(
            "Labels this project uses: " + ", ".join(name for name, _ in profile.common_labels[:8])
        )
    parts.append("")

    if context and context.prior:
        parts.append("Related tickets that already exist. Reference them where relevant,")
        parts.append("and do not restate work one of them already covers:")
        for art in context.prior[:4]:
            parts.append(f"- {art.match.ticket.key}: {art.match.ticket.title}")
        parts.append("")

    if context and context.touched:
        parts.append("Files that merge requests for those tickets changed. These are")
        parts.append("where this work most likely lands; name them if it helps:")
        parts.extend(f"- {path}" for path in context.touched[:8])
        parts.append("")

    if context and context.files:
        parts.append("Files in the checkout mentioning the subject (a text search,")
        parts.append("so some are irrelevant):")
        parts.extend(f"- {hit.path}" for hit in context.files[:6])
        parts.append("")

    return "\n".join(parts).strip()


def _revision_prompt(body: str, review: Review) -> str:
    """The findings, handed back as instructions.

    Each one already carries the count that justifies it, so the model is
    being corrected by the corpus rather than by an opinion.

    The draft is fenced and the instructions are kept away from it, because a
    3B model given feedback and a draft in one flat block copied the feedback
    into the ticket: a real run came back containing the sentence "Anyone will
    know it did when they see...", which is the wording of a finding's `fix`,
    not anything about the bug. Marking which part is the document and saying
    outright that the rest is not text to reuse fixed it.
    """
    lines = [
        "Below is a draft ticket, then a list of ways it fell short of the team's own tickets.",
        "",
        "Rewrite the draft so that every point is addressed. Keep everything "
        "else as it is. Output only the rewritten ticket - none of the "
        "feedback wording belongs in it.",
        "",
        "=== DRAFT ===",
        body,
        "=== END DRAFT ===",
        "",
        "What it got wrong:",
    ]
    lines.extend(f"- {finding.what} {finding.why} {finding.fix}" for finding in review.findings)
    return "\n".join(lines)


_SETEXT_RULE = re.compile(r"^(=|-){3,}\s*$")


def _drop_repeated_title(title: str, text: str) -> str:
    """Take the title back off the front of the body, if the model put it there.

    The system prompt says to write the description only, and llama3.2 opened a
    draft with the ticket title underlined in `=` anyway. The body goes into
    the description field, where the title is already displayed directly above
    it, so a copy of it is noise in every ticket the tool writes.

    Only a first line that *is* the title comes off - matched on the same
    normalisation headings use, so punctuation and case do not save it. A first
    line that says something else is the model's own opening sentence and it
    stays, whatever it looks like.
    """
    body = (text or "").strip()
    if not body:
        return body
    lines = body.splitlines()
    first = lines[0].lstrip("#").strip()
    if normalise_heading(first) != normalise_heading(title):
        return body
    rest = lines[1:]
    # A setext underline belongs to the line above it and goes with it.
    if rest and _SETEXT_RULE.match(rest[0].strip()):
        rest = rest[1:]
    return "\n".join(rest).strip()


def compose(
    title: str,
    profile: Profile,
    writer: Writer,
    *,
    context: Context | None = None,
    labels: tuple[str, ...] = (),
    revisions: int = REVISIONS,
) -> Composed:
    """Write a ticket body for `title`, then mark it and let it fix itself once."""
    language = {"de": "German", "en": "English"}.get(profile.language or "", "English")
    system = SYSTEM.format(language=language, target=round(profile.chars_median) or 800)

    body = _drop_repeated_title(title, writer.write(system, build_prompt(title, profile, context)))
    review = _review_draft(title, body, profile, labels=labels)
    attempts = 1

    while attempts <= revisions and review.findings:
        revised = _drop_repeated_title(title, writer.write(system, _revision_prompt(body, review)))
        attempts += 1
        candidate = _review_draft(title, revised, profile, labels=labels)
        # Keep the better of the two. A revision that scores worse is a
        # revision that misunderstood, and shipping it because it came second
        # would make the loop actively harmful.
        if (candidate.alignment or 0) >= (review.alignment or 0):
            body, review = revised, candidate

    return Composed(
        title=title,
        body=body,
        review=review,
        attempts=attempts,
        model=getattr(writer, "model", writer.name),
    )
