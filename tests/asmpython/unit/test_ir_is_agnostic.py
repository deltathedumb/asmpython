"""The IR must not know which language is above it or which machine below.

`ir/__init__.py` opens by calling UIR "a Universal Intermediate
Representation", and `opcodes.py` promises a backend author reads that file and
nothing else. Both are claims about what the package does NOT contain, and a
claim like that decays silently: nobody notices the day a Python-shaped helper
lands in `ir/`, and by the time a second frontend arrives it is load-bearing.

IT HAD ALREADY DECAYED ONCE, which is why this file exists. `ir/` held
`objects_host.py` (15,336 lines), `hostsvc_host.py` and `natives_host.py` --
16,168 lines of Python object model against 1,568 lines of actual IR, so 91% of
the package was the thing the package said it knew nothing about. Worse,
`objects_host.py` imported `asmpython.frontends.python.methods`: the bottom
layer of the compiler importing the top. They live under `objects/` now, beside
the C and subset arrangements of the same runtime.

WHAT IS CHECKED, and why each one:

  * THE IR PROPER IMPORTS ONLY `diagnostics`. That is the whole dependency a
    span needs. Anything else is a layer above reaching down.
  * IT NEVER SAYS `apy_`. The object runtime's prefix appearing in `ir/` means
    an opcode, a verifier rule or a printer special case has been shaped around
    Python values.

`interpreter.py` IS THE ONE EXEMPTION, and a temporary one. It resolves
`apy_*`, `plat_*`, the host services and the native symbols by importing each
provider directly, which is the last place in `ir/` that names Python. Each of
those providers already answers `NOT_MINE` for a name it does not claim -- the
protocol for injecting them is written, it is simply not used -- so the fix is
to hand the interpreter its hosts rather than have it import them. Until then
this file states the exemption instead of letting it pass unremarked.
"""
from __future__ import annotations

import re
from pathlib import Path

IR = Path(__file__).resolve().parents[3] / "src" / "asmpython" / "ir"

#: `interpreter.py` -- see the module docstring. Anything else added here is a
#: decision to make the IR know about its callers, and should be argued for in
#: a commit message rather than in this tuple.
EXEMPT = ("interpreter.py",)

#: The one package the IR may reach for: a span, so a verifier error can point
#: at the source. It carries no language and no machine.
ALLOWED_IMPORTS = {"diagnostics"}

_IMPORT = re.compile(r"^\s*from\s+\.+(\w+)", re.M)


def _ir_modules():
    return sorted(p for p in IR.glob("*.py") if p.name not in EXEMPT)


class TestTheIRKnowsNothingAboveOrBelowIt:
    def test_it_imports_nothing_but_diagnostics(self):
        reached = {}
        for path in _ir_modules():
            for name in _IMPORT.findall(path.read_text()):
                if name not in ALLOWED_IMPORTS and not (IR / f"{name}.py").exists():
                    reached.setdefault(path.name, set()).add(name)
        assert not reached, (
            "the IR reaches outside itself:\n  "
            + "\n  ".join(f"{f} -> {sorted(n)}" for f, n in sorted(reached.items()))
            + "\n\nUIR is meant to be portable between frontends. A package "
              "it imports is a package a second frontend inherits.")

    def test_it_never_names_the_python_object_runtime(self):
        guilty = {
            path.name: len(re.findall(r"apy_", path.read_text()))
            for path in _ir_modules()
            if "apy_" in path.read_text()
        }
        assert not guilty, (
            f"`apy_` appears in the IR: {guilty}\n\n"
            "That prefix is the Python object runtime's. In `ir/` it means an "
            "opcode, a verifier rule or a printer case has been shaped around "
            "Python values -- which is the shape the next frontend cannot use.")

    def test_the_exemption_is_still_only_the_interpreter(self):
        """A ratchet in both directions: a file that no longer needs the
        exemption must come off the list, or the list stops meaning anything."""
        for name in EXEMPT:
            path = IR / name
            assert path.exists(), f"{name} is exempted and does not exist"
            assert "apy_" in path.read_text(), (
                f"{name} no longer names the object runtime -- take it out of "
                f"EXEMPT so the check covers it.")
