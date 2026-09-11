# SPDX-License-Identifier: MIT

"""The changelog section for one version, as release notes.

A file rather than a heredoc inside the workflow. The first version of this
was Python indented inside a YAML `run:` block, where the closing `PY` was
indented too - so the here-document never terminated and the interpreter got
an IndentationError from the first line. Neither the YAML parser nor a grep
notices that; running it does.

It exists at all so there is one text. Release notes written separately from
the changelog are a second thing to keep true, and the second thing is the one
that goes stale.
"""

from __future__ import annotations

import pathlib
import sys


def notes(changelog: str, version: str) -> str:
    """Everything under `## [version]`, without the heading itself."""
    marker = f"## [{version}]"
    if marker not in changelog:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    rest = changelog[changelog.index(marker) :]
    # The next version heading ends it; a changelog with one entry does not
    # have one, so the end of the file does.
    end = rest.find("\n## [", len(marker))
    section = (rest[:end] if end != -1 else rest).strip()
    body = section.split("\n", 1)
    if len(body) == 1:
        raise SystemExit(f"the section for {version} is a heading and nothing else")
    return body[1].strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py <tag or version>", file=sys.stderr)
        return 2
    version = argv[1].lstrip("v")
    changelog = pathlib.Path("CHANGELOG.md").read_text(encoding="utf-8")
    print(notes(changelog, version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
