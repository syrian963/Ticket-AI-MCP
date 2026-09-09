# SPDX-License-Identifier: MIT

"""Where the credentials and the project come from.

Environment only. No config file, no keyring, no token argument on the command
line - a token in `--token` ends up in shell history and in the process list,
and the one place it is genuinely awkward to leak from is the environment an
MCP client already has to populate.

The error messages here get more care than the code does. "Missing
configuration" sends someone to the README; naming the variable and what to put
in it does not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .trackers import Tracker, TrackerError, build

CACHE_DIR_VAR = "TICKET_AI_CACHE_DIR"


@dataclass(frozen=True, slots=True)
class Settings:
    tracker: str
    project: str

    @property
    def slug(self) -> str:
        """A filename-safe name for this tracker and project pair."""
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in self.project)
        return f"{self.tracker}-{safe}".strip("-")


def _require(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise TrackerError(f"{name} is not set. {hint}")
    return value


def settings(tracker: str | None = None, project: str | None = None) -> Settings:
    """Resolve which tracker and project to work on.

    Arguments win over the environment so one server can serve several projects
    in a session without being restarted - which is the normal case for an
    assistant that has just been asked about a different repository.
    """
    name = (tracker or os.environ.get("TICKET_AI_TRACKER", "")).strip().lower()
    if not name:
        raise TrackerError(
            "No tracker chosen. Set TICKET_AI_TRACKER to gitlab, jira or github, "
            "or pass it with the call."
        )
    target = (project or os.environ.get("TICKET_AI_PROJECT", "")).strip()
    if not target:
        raise TrackerError(
            "No project chosen. Set TICKET_AI_PROJECT - a GitLab path like "
            "acme/shop or its numeric id, a Jira project key like PROJ, or a "
            "GitHub owner/repo."
        )
    return Settings(tracker=name, project=target)


def tracker_for(name: str) -> Tracker:
    """Build the named adapter from the environment."""
    if name == "gitlab":
        return build(
            "gitlab",
            url=_require(
                "TICKET_AI_GITLAB_URL",
                "Point it at your instance, for example https://gitlab.example.com.",
            ),
            token=_require(
                "TICKET_AI_GITLAB_TOKEN",
                "A personal access token with the read_api scope is enough.",
            ),
        )
    if name == "jira":
        # Email and token are read but not required: a public Jira answers
        # without them, and the adapter says what to set if this one will not.
        return build(
            "jira",
            url=_require(
                "TICKET_AI_JIRA_URL",
                "For example https://acme.atlassian.net.",
            ),
            email=os.environ.get("TICKET_AI_JIRA_EMAIL", "").strip(),
            token=os.environ.get("TICKET_AI_JIRA_TOKEN", "").strip(),
            # Cloud and self-hosted are detected from the instance itself.
            # The override exists for the instance that answers serverInfo
            # from behind a proxy that lies about it.
            api=os.environ.get("TICKET_AI_JIRA_API", "auto").strip() or "auto",
        )
    if name == "github":
        return build(
            "github",
            token=_require(
                "TICKET_AI_GITHUB_TOKEN",
                "A token with read access to the repository's issues.",
            ),
        )
    return build(name)


def writer_for(name: str | None = None, model: str | None = None):
    """Build the configured model backend, or say there is none.

    Returns None rather than raising when nothing is configured, because that
    is the normal state: everything except composing works without a model,
    and the tool should not treat its absence as a fault.
    """
    from .writers import build as build_writer

    chosen = (name or os.environ.get("TICKET_AI_WRITER", "")).strip().lower()
    if not chosen or chosen == "none":
        return None
    return build_writer(chosen, **({"model": model} if model else {}))


def cache_dir() -> Path:
    """Where a built profile is kept between runs.

    Under the working directory by default, so a profile lives next to the
    checkout it describes and disappears with it.
    """
    root = Path(os.environ.get(CACHE_DIR_VAR, "")).expanduser() or Path.cwd() / ".ticket-ai"
    root.mkdir(parents=True, exist_ok=True)
    return root
