"""One command-line option a component takes.

SHARED BY ALL THREE KINDS, which is why it lives here rather than with the
backends. A backend has always been able to declare its own flags; a frontend
and a linker could not, so the flags they need sat on the driver's own parser
instead -- `--import-path` and `--host-python` are the Python frontend's, and
`--link-input` is the linker's, and the driver carried all three as if they
were facts about compiling in general.

WHY A DECLARATION AND NOT A PARSER ENTRY. A component that ships outside this
repository gets its flags the same way a built-in does, and the driver can
say what any component accepts without knowing what any of them mean.

TWO SPELLINGS, ALWAYS. `--opt-level` is what a user types; `--pybc:opt-level`
is what they type when two components want that name. The namespaced form is
registered whether or not there is a collision, so a script can be written
against the unambiguous spelling and keep working when a plugin later claims
the short one.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Option:
    """One command-line option a component takes."""

    #: As typed, without the dashes: "class-version" is `--class-version`.
    name: str
    help: str
    #: What the value is called in `--help`.
    metavar: str = "VALUE"
    #: True when the flag may be given more than once and every value counts.
    #: The component is then handed a LIST rather than a string.
    #:
    #: NOT EVERY OPTION IS ONE VALUE, and the two flags this module's own
    #: docstring names as belonging to a frontend -- `--import-path` and a C
    #: frontend's `--include-path` -- are both search paths, which are lists
    #: by nature. Without this they would have to be one string with a
    #: separator in it, and `-D NAME=a,b` shows why that does not generalise:
    #: a macro body may contain any separator you pick.
    repeat: bool = False

    #: A ONE-LETTER SPELLING, without the dash: "P" is `-P`. Rare and worth
    #: being rare -- there are 26 of them and no namespace to escape into, so
    #: the qualified form cannot rescue a short flag two components both
    #: want. Declared because `-P` is CPython's own flag spelled the same
    #: way, and a Python frontend that could not offer it would be offering
    #: something else.
    short: str | None = None

    #: A SWITCH TAKES NO VALUE: present or absent is the whole of it, and
    #: what the component is handed is True. `--library` is one, and writing
    #: it as a value-taking option would have made `--library yes` the
    #: spelling of something every other compiler spells `--library`.
    switch: bool = False

    @property
    def flag(self) -> str:
        return "--" + self.name

    def qualified(self, owner: str) -> str:
        """The always-unambiguous spelling: `--jvm:class-version`.

        THE OWNER'S NAME AND NOT ITS KIND, because two KINDS can share a name
        -- `cpyext` is both a backend and a linker -- and what a reader wants
        to disambiguate is which component, not which stage.
        """
        return f"--{owner}:{self.name}"


class OptionError(Exception):
    """An option value nobody can act on.

    Distinct from a crash: the value is the user's and the message says what
    was wrong with it, so the driver reports it as a diagnostic rather than
    letting a traceback out.
    """
