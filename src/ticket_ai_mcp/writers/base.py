# SPDX-License-Identifier: MIT

"""Where a language model plugs in, for the one job that needs one.

Everything else in this tool counts. Counting cannot produce a paragraph of
German, so writing a ticket needs a model - and the point of this package is
that **which** model is the user's decision, not the tool's.

The interface is one method. A writer takes a prompt and returns text. It does
not know what a ticket is, does not build the prompt, and does not judge the
result: the prompt is assembled in `compose.py` from measurements, and the
result goes back through `review_draft` like anything else. That separation is
what lets a small local model produce an acceptable ticket - it is writing into
a shape the tool already worked out, and being marked against a corpus
afterwards.

No writer is required. With none configured the tool behaves exactly as before,
which is the default and stays the default: a key or a running model is
something a user opts into, never something the tool needs to function.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable


class WriterError(RuntimeError):
    """A writer could not produce text.

    Carries what to do about it. The failures here are all configuration -
    a model name the account cannot reach, a token without the right scope, a
    local server that is not running - and each has a different fix.
    """


@runtime_checkable
class Writer(Protocol):
    name: ClassVar[str]

    def write(self, system: str, prompt: str) -> str:
        """Answer the prompt. Plain text in, plain text out."""
        ...

    def models(self) -> list[str]:
        """Model identifiers this backend can reach, best effort.

        Empty is a valid answer: not every backend can enumerate, and a wrong
        list is worse than none.
        """
        ...


_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    _REGISTRY[cls.name] = cls
    return cls


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build(name: str, **config: Any) -> Writer:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        known = ", ".join(available()) or "none"
        raise WriterError(
            f"unknown writer {name!r}. This build knows: {known}. "
            "Leave TICKET_AI_WRITER unset to run without one."
        ) from None
    return cls(**config)
