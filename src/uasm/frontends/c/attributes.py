"""C23's `[[...]]` attributes: which exist, and what each one does here.

THE SPELLING IS NEW AND THE IDEA IS NOT. `__attribute__((...))` has been in
every real header for thirty years and `parser._attributes` has always parsed
and discarded it; C23 standardised a syntax and a handful of names, and the
difference that matters is that these names have MEANINGS a program may rely
on rather than being a vendor's advice.

THE NUMBER IS WHAT `__has_c_attribute` ANSWERS, and it is a date rather than a
1 because C versions its attributes: a program that wants the `nodiscard` with
a message tests for `>= 202003L` and gets a useful answer from an
implementation that has the older one. The values are the standard's.

WHAT IS NOT HERE IS IGNORED, and C says so: an implementation ignores an
attribute it does not know, which is what makes the syntax safe to use in a
header that has to compile everywhere. A PREFIXED one -- `[[gnu::packed]]` --
is ignored silently, because the prefix says it belongs to somebody else. An
unprefixed one this file has never heard of gets a warning, because the
unprefixed namespace is the standard's and a name in it is either a typo or a
newer C than this.
"""
from __future__ import annotations

#: name -> the value `__has_c_attribute` answers for it.
STANDARD: dict[str, int] = {
    "deprecated": 201904,
    "fallthrough": 201904,
    "maybe_unused": 201904,
    "nodiscard": 202003,
    "noreturn": 202202,
    "_Noreturn": 202202,
    "unsequenced": 202207,
    "reproducible": 202207,
}

#: The ones with an effect here, and what it is. Everything else in
#: `STANDARD` is recognised, accepted and does nothing -- which is not a gap:
#: `maybe_unused` silences a warning this frontend does not emit, and
#: `fallthrough` silences another one. Saying so is better than a warning
#: about an attribute that is doing exactly what the program asked.
ACTS: frozenset[str] = frozenset({"noreturn", "_Noreturn", "nodiscard",
                                  "deprecated"})
