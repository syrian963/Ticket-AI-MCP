# SPDX-License-Identifier: MIT

"""The cases, and the rules about what may become one.

A case is a title that a real team actually wrote a ticket for, plus the board
profile as it stood when the case was collected. Both halves are frozen on
disk. The profile is the reason: `compose` writes into whatever shape the
profile describes, so a profile that was relearned between two runs changes the
score without anything about the model changing, and the comparison is then
measuring the wrong thing.

The reference body - what the team really shipped - is stored but not scored
here. It is not a right answer. Two people write a usable ticket for the same
title in two different ways, and marking a draft down for differing from one of
them would measure imitation. It is kept because a human rating a draft in a
later pass needs to see what this board considers normal.

**Only public boards.** Every case carries the URL it came from and has to be
readable without credentials. This is not a licensing nicety: a dataset
assembled from an employer's tracker is their confidential information, and no
amount of anonymising changes that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..profile import Profile

DATASET_DIR = Path(__file__).resolve().parents[3] / "evals" / "dataset"

BOARD_FILE = "board.json"
PROFILE_FILE = "profile.json"
CASES_FILE = "cases.jsonl"

NEWLINE = chr(10)


def jsonl_lines(text: str) -> list[str]:
    """Split JSONL on newlines only, which `str.splitlines` does not do.

    `splitlines` also breaks on U+2028, U+2029, U+0085 and three ASCII
    separators, and `json.dumps` escapes none of them. A German ticket in the
    kern-ux board contains a literal U+2028, so the file was valid JSONL and
    the reader cut one record in half and reported it as invalid JSON.

    Nothing about the failure pointed at the reader: the message named the
    file, the line number and a column, and the record it quoted really was
    truncated. Anything reading a record per line has to use this.
    """
    return text.split(NEWLINE)


class DatasetError(RuntimeError):
    """A case or board on disk is not usable, and says which one."""


@dataclass(frozen=True, slots=True)
class Case:
    """One title, and what the team wrote for it."""

    key: str
    title: str
    url: str
    reference: str
    labels: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, where: str) -> Case:
        missing = [f for f in ("key", "title", "url", "reference") if not data.get(f)]
        if missing:
            raise DatasetError(f"{where}: case is missing {', '.join(missing)}")
        if not data["url"].startswith(("http://", "https://")):
            # A case with no reachable source cannot be checked by anyone
            # else, which is most of the point of publishing the dataset.
            raise DatasetError(f"{where}: case {data['key']} has no public url")
        return cls(
            key=str(data["key"]),
            title=str(data["title"]),
            url=str(data["url"]),
            reference=str(data["reference"]),
            labels=tuple(data.get("labels") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "url": self.url,
            "labels": list(self.labels),
            "reference": self.reference,
        }


@dataclass(frozen=True, slots=True)
class Board:
    """A public board, its frozen profile, and the cases taken from it.

    `slug` is the directory name and the identifier used in results. It is not
    derived from the project path at read time, because renaming a project
    upstream would then silently split one board's history into two.
    """

    slug: str
    tracker: str
    project: str
    url: str
    fetched_at: str
    profile: Profile
    cases: tuple[Case, ...]

    @property
    def language(self) -> str:
        return self.profile.language or "en"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"{path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path} is not valid JSON: {exc}") from exc


def load_board(directory: Path) -> Board:
    """Read one board directory, or say precisely what is wrong with it."""
    meta = _read_json(directory / BOARD_FILE)
    for field_name in ("tracker", "project", "url", "fetched_at"):
        if not meta.get(field_name):
            raise DatasetError(f"{directory / BOARD_FILE}: missing {field_name}")

    profile_path = directory / PROFILE_FILE
    try:
        profile = Profile.from_json(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"{profile_path} does not exist") from exc
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DatasetError(f"{profile_path} is not a profile: {exc}") from exc

    cases_path = directory / CASES_FILE
    if not cases_path.exists():
        raise DatasetError(f"{cases_path} does not exist")

    cases: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(jsonl_lines(cases_path.read_text(encoding="utf-8")), start=1):
        if not line.strip():
            continue
        where = f"{cases_path}:{number}"
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{where}: not valid JSON: {exc}") from exc
        case = Case.from_dict(payload, where=where)
        if case.key in seen:
            raise DatasetError(f"{where}: duplicate case key {case.key}")
        seen.add(case.key)
        cases.append(case)

    if not cases:
        raise DatasetError(f"{cases_path} has no cases")

    return Board(
        slug=directory.name,
        tracker=str(meta["tracker"]),
        project=str(meta["project"]),
        url=str(meta["url"]),
        fetched_at=str(meta["fetched_at"]),
        profile=profile,
        cases=tuple(cases),
    )


def load_suite(root: Path | None = None) -> tuple[Board, ...]:
    """Every board in the dataset, in a fixed order.

    Sorted by slug rather than by whatever order the filesystem hands back, so
    that two runs on two machines produce results in the same sequence and a
    diff of two result files is readable.
    """
    base = root or DATASET_DIR
    if not base.exists():
        raise DatasetError(f"{base} does not exist")
    directories = sorted(p for p in base.iterdir() if p.is_dir() and (p / BOARD_FILE).exists())
    if not directories:
        raise DatasetError(f"{base} contains no boards")
    return tuple(load_board(d) for d in directories)


def write_board(
    directory: Path,
    *,
    tracker: str,
    project: str,
    url: str,
    fetched_at: str,
    profile: Profile,
    cases: list[Case],
) -> None:
    """Write a board out in the shape `load_board` expects.

    Used by the collection tool in `tools/`, which runs once and by hand. The
    output is committed; nothing in the evaluation path writes here.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / BOARD_FILE).write_text(
        json.dumps(
            {"tracker": tracker, "project": project, "url": url, "fetched_at": fetched_at},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / PROFILE_FILE).write_text(profile.to_json(), encoding="utf-8")
    lines = [json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True) for case in cases]
    (directory / CASES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
