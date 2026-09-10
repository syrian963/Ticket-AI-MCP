# SPDX-License-Identifier: MIT

"""The template the project *declares*, as opposed to the one it practises.

Everything else here measures what a team does. This reads what a team said it
would do - the issue forms sitting in the repository - and the interesting
thing is almost never either one on its own. It is the gap:

    Initial Checks    declared required   ->  87% of shipped tickets have it
    Example Code      declared optional   ->  87%
    Steps to reproduce  declared required ->  31%

A required field at 31% is not a discipline problem. It is a form asking for
something people cannot supply, and the fix is to change the form. Nothing in
this tool could say that before, because it never read the form.

Three shapes are handled, because that is what projects have:

- **GitHub issue forms** (`.github/ISSUE_TEMPLATE/*.yml`) - structured, with a
  `required` flag that makes the comparison sharp.
- **GitHub markdown templates** (`.github/ISSUE_TEMPLATE/*.md`) - headings
  only, no requiredness.
- **GitLab** (`.gitlab/issue_templates/*.md`) - the same, in a different place.

Parsed rather than imported: `yaml` is not a dependency of this project and
adding one to read four keys out of a file would be a poor trade. The parser
below reads exactly the two fields that matter and ignores the rest, which is
also why it cannot be confused by whatever else a form declares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .textstats import normalise_heading

# Where projects keep them. Order matters only for reporting.
SEARCH = (
    ".github/ISSUE_TEMPLATE",
    ".github/issue_template",
    ".gitlab/issue_templates",
)

# A markdown heading in a template file. Templates use `##` and bold lines the
# same way tickets do, so the heading rules live in one place.
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_BOLD_HEADING = re.compile(r"^\s{0,3}(?:\*\*|__)(.{2,80}?)(?:\*\*|__)\s*:?\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Field:
    """One thing a form asks for."""

    label: str
    key: str
    required: bool
    source: str


@dataclass(frozen=True, slots=True)
class Declared:
    """Everything the repository's own forms ask for."""

    fields: tuple[Field, ...] = ()
    files: tuple[str, ...] = ()

    @property
    def required(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.required)

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


def _yaml_fields(text: str, source: str) -> list[Field]:
    """Pull `label:` and `required:` out of a GitHub issue form.

    A hand-rolled scan rather than a YAML parser, because the alternative is a
    dependency for four keys. What it tracks is the **list item**, not the
    order of the keys inside it: a form entry begins at `- type:` and both
    `attributes.label` and `validations.required` belong to whichever entry
    they sit in, in whatever order the author wrote them.

    That distinction is the whole of a bug this had. The first version assumed
    `required` always came after its `label`, and home-assistant/core - the
    project whose mandatory form this tool's own documentation cites - writes
    `validations:` first:

        - type: textarea
          validations:
            required: true
          attributes:
            label: The problem

    Every one of their required fields was read as optional, so a form nobody
    can submit half-empty looked like a form nobody has to fill in.
    """
    found: list[Field] = []
    label: str | None = None
    required = False

    def flush() -> None:
        nonlocal label, required
        if label:
            found.append(Field(label, normalise_heading(label), required, source))
        label = None
        required = False

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- type:") or line.startswith("-type:"):
            flush()
            continue
        if line.startswith("label:"):
            # Two labels inside one item would be malformed; treat the second
            # as a new field rather than losing it.
            if label:
                flush()
            label = line[len("label:") :].strip().strip("\"'")
        elif line.startswith("required:"):
            required = line[len("required:") :].strip().lower() in ("true", "yes")
    flush()
    return [f for f in found if f.key]


def _markdown_fields(text: str, source: str) -> list[Field]:
    """Headings in a markdown template.

    Markdown templates carry no requiredness, so every field comes back
    optional. Saying "declared, not required" is honest; inventing a
    requirement the file does not state would put words in the project's mouth.
    """
    headings = [m.group(1) for m in _MD_HEADING.finditer(text)]
    headings += [m.group(1) for m in _BOLD_HEADING.finditer(text)]
    seen: dict[str, str] = {}
    for h in headings:
        key = normalise_heading(h.rstrip(":").strip())
        if key:
            seen.setdefault(key, h.strip())
    return [Field(label, key, required=False, source=source) for key, label in seen.items()]


def read(root: Path) -> Declared:
    """Find and parse whatever issue templates a checkout carries.

    An empty result is the common case and not a fault: plenty of projects
    have no declared template at all, and for those the measured profile is
    the only contract there is.
    """
    if not root or not root.is_dir():
        return Declared()

    fields: list[Field] = []
    files: list[str] = []
    for folder in SEARCH:
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            # config.yml routes people elsewhere; it asks for nothing.
            if path.name == "config.yml" or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            source = path.name
            if path.suffix in (".yml", ".yaml"):
                found = _yaml_fields(text, source)
            elif path.suffix in (".md", ".markdown"):
                found = _markdown_fields(text, source)
            else:
                continue
            # Recorded because it was read, not because it yielded something.
            # denoland/deno's markdown template is front-matter and one line
            # with no headings in it, and skipping it here made the report say
            # "no issue template found in the checkout" about a repository that
            # has two - which is a different problem with a different fix.
            files.append(f"{folder}/{path.name}")
            fields.extend(found)

    # One field per key. A project with a bug form and a feature form declares
    # `Description` twice, and it is one thing being asked for; required in
    # either form counts as required.
    merged: dict[str, Field] = {}
    for f in fields:
        existing = merged.get(f.key)
        if existing is None:
            merged[f.key] = f
        elif f.required and not existing.required:
            merged[f.key] = f
    return Declared(fields=tuple(merged.values()), files=tuple(files))


@dataclass(frozen=True, slots=True)
class Gap:
    """One field, as declared and as practised."""

    label: str
    key: str
    required: bool
    rate: float
    declared_only: bool
    # Which form file asked for it. A project with a bug form and a task form
    # has two contracts, and this tool measures one corpus against both: on
    # home-assistant/core the task form requires a Description that no bug
    # ticket has, which reads as a field nobody fills in until you know it
    # belongs to a form those tickets never used.
    source: str = ""

    @property
    def is_ignored(self) -> bool:
        """Asked for, and largely not supplied."""
        return self.rate < 0.5


def compare(declared: Declared, profile) -> tuple[tuple[Gap, ...], tuple[str, ...]]:
    """Line the declared form up against the measured corpus.

    Returns the gaps, and the headings people write that no form asked for -
    the second being the more interesting direction. A section that appears in
    most tickets and in no template is a convention the project grew and never
    wrote down, and it is the first thing to add to the form.
    """
    by_key = {s.key: s for s in profile.sections}
    gaps = tuple(
        Gap(
            label=f.label,
            key=f.key,
            required=f.required,
            rate=by_key[f.key].rate if f.key in by_key else 0.0,
            declared_only=f.key not in by_key,
            source=f.source,
        )
        for f in declared.fields
    )
    declared_keys = {f.key for f in declared.fields}
    undeclared = tuple(
        s.heading for s in profile.sections if s.key not in declared_keys and s.rate >= 0.5
    )
    return gaps, undeclared
