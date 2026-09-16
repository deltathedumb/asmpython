"""Which frontend, backend and linker a build means.

THE SPELLING CHOOSES, and a name typed on the command line only overrules it.
`asmpython build thing.py -o thing.wasm` is a complete request: `.py` is the
Python frontend and `.wasm` is the wasm backend, and neither had to be named.
This is the whole of `-bk`, `-fr` and `-ln` being optional.

THREE QUESTIONS IN ONE ORDER, because the answers depend on each other:

  * the FRONTEND from the source's extension, which is independent;
  * the LINKER from the output's, because what the user names with `-o` is
    the program and not the artifact on the way to it -- `-o thing.so` is an
    extension module, and the `.c` in between is nobody's business;
  * the BACKEND from the linker, which declares what it can take input from,
    falling back to the output's extension when no linker claimed it.

AMBIGUITY IS AN ERROR AND NEVER A GUESS. Two components claiming one
extension is a question this cannot answer, and a warning followed by a build
of the wrong thing is worse than stopping: the user reads the warning after
the artifact already exists. So it raises, names every candidate, and says
which flag settles it.

A PREFERENCE IS NOT AN AMBIGUITY. `-o thing` with no extension at all names
no linker and no backend, and `cc` listing `c` before the machine backends is
a declared preference rather than a tie -- see `Toolchain.backends`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..backend.families import SelectionError


@dataclass(frozen=True)
class Choice:
    """The three components one build runs through."""

    frontend: str
    backend: str
    linker: str


def _claimants(suffix: str, registry, attr: str) -> list[str]:
    """Every registered component whose `attr` lists `suffix`, sorted."""
    if not suffix:
        return []
    return sorted(
        name for name, component in registry.available().items()
        if suffix in getattr(component, attr, ()))


def _one(candidates: list[str], *, what: str, spelling: str,
         flag: str) -> str | None:
    """The single candidate, or None for no candidate at all.

    RAISES ON TWO OR MORE rather than taking the first. Sorting them makes
    first-wins look deliberate when it is alphabetical, which is exactly the
    kind of decision a reader would believe and should not.
    """
    if not candidates:
        return None
    if len(candidates) > 1:
        raise SelectionError(
            f"{spelling!r} does not say which {what}: "
            f"{', '.join(candidates)} all produce it; "
            f"pick one with {flag}")
    return candidates[0]


def choose_frontend(source: Path, named: str | None, registry) -> str:
    if named:
        return named
    known = _claimants(source.suffix, registry, "extensions")
    picked = _one(known, what="frontend", spelling=source.suffix,
                  flag="-fr/--frontend")
    if picked is None:
        every = sorted(
            ext for fe in registry.available().values()
            for ext in fe.extensions)
        raise SelectionError(
            f"no frontend reads {source.suffix or 'a file with no extension'!r}"
            f" (the registered ones read: {', '.join(every) or 'nothing'}); "
            f"name one with -fr/--frontend")
    return picked


def choose_linker(output: Path | None, named: str | None, registry,
                  *, fallback: str) -> str:
    """The linker the OUTPUT names, or `fallback` when nothing claims it.

    `fallback` IS THE CALLER'S TO DECIDE because the honest answer differs by
    verb: a build with no `-o` at all is producing a program and wants the
    native toolchain, while one whose output extension is a backend's own
    artifact wants `none` -- the backend has already produced the file being
    asked for and there is nothing left to link.
    """
    if named:
        return named
    if output is None:
        return fallback
    picked = _one(_claimants(output.suffix, registry, "artifacts"),
                  what="linker", spelling=output.suffix, flag="-ln/--linker")
    return picked if picked is not None else fallback


def choose_backend(output: Path | None, named: str | None, linker: str,
                   backends, linkers) -> str:
    """The backend, from the linker first and the output's spelling second.

    THE LINKER KNOWS BEST. It declares what it can take input from, in
    preference order, so `-o thing.so` reaches the `cpyext` backend rather
    than `c` -- both write `.c`, and the extension alone cannot tell them
    apart.

    WHICH IS WHY THE OUTPUT IS THE SECOND QUESTION AND NOT THE FIRST: it is
    asked only for a linker that takes anything, which is `none`, and there
    the output really is the backend's own artifact.
    """
    if named:
        return named
    wanted = getattr(linkers.get(linker), "backends", ())
    if wanted:
        ready = [b for b in wanted if b in backends.available()]
        if not ready:
            raise SelectionError(
                f"the {linker} linker takes input from "
                f"{', '.join(wanted)}, and none of them is registered")
        return ready[0]
    if output is not None:
        picked = _one(_claimants(output.suffix, backends, "artifacts"),
                      what="backend", spelling=output.suffix,
                      flag="-bk/--backend")
        if picked is not None:
            return picked
    raise SelectionError(
        f"the {linker} linker takes input from any backend and "
        f"{'the output names none' if output is None else repr(output.suffix)}"
        f" does not say which; pick one with -bk/--backend")


def choose(source: Path, output: Path | None, *, frontend: str | None,
           backend: str | None, linker: str | None, emit: bool,
           frontends, backends, linkers) -> Choice:
    """The three components one build runs through.

    WHAT `-o` MEANS DECIDES WHETHER THERE IS A LINKER AT ALL, and the four
    cases are worth spelling out because they are the whole policy:

      * `--emit` says not to link, so there is nothing to choose;
      * an output a LINKER claims is a program -- `.so`, `.jar`, `.pyc`;
      * an output a BACKEND claims is an artifact the user asked for by
        name, and asking a linker to turn `out.c` into a program named
        `out.c` is not what was meant;
      * anything else -- `-o thing`, or no `-o` at all -- is a program, and
        the native toolchain is what makes one.

    THE THIRD CASE IS THE ONE THAT USED TO BE WRONG. With the toolchain
    fixed at `cc`, `-o out.c` linked an executable and called it `out.c`.
    """
    fe = choose_frontend(source, frontend, frontends)
    # THE SPELLING NAMES THE PIPELINE EVEN WHEN NOTHING WILL BE LINKED, which
    # is why this is asked before `emit` is looked at. `--emit -o thing.so`
    # wants the artifact of the pipeline that MAKES a `.so`, and forcing the
    # linker to `none` first threw that away: `.so` is claimed by the cpyext
    # TOOLCHAIN and by no backend, so the backend question then had nothing
    # to go on and refused a request that is perfectly clear.
    names = choose_linker(
        output, linker, linkers,
        # An artifact the backend itself writes needs no linker; a program
        # does. `cc` is named here rather than derived because "the one that
        # makes a native executable" is a fact about this driver's host and
        # not something a registry can be asked.
        fallback=("none" if (output is not None
                             and _claimants(output.suffix, backends,
                                            "artifacts"))
                  else "cc"))
    return Choice(
        frontend=fe,
        backend=choose_backend(output, backend, names, backends, linkers),
        # `--emit` TRUNCATES THE PIPELINE, it does not choose a different
        # one. An explicitly named linker still wins, because a user who
        # types both has said which they meant.
        linker=(linker or "none") if emit else names)
