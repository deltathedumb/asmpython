"""The cycle collector -- the half of Python's memory story that plain
reference counting cannot do.

COVERAGE: `collect()`, which finds every object kept alive ONLY by a
reference cycle, runs its `__del__`, fires its weak references, and
answers how many objects it collected. `isenabled()`, which answers
True: this collector only ever runs when a program asks for it, and
saying otherwise would be a lie in the direction that matters (a
program that reads `isenabled()` to decide whether it must call
`collect()` itself needs the truthful answer, which is that it must).

WHY THIS EXISTS AT ALL, and why it is separate from `__del__`: a cycle's
members hold each other's reference counts above zero, so no amount of
reference counting collects one. `a.other = b; b.other = a` then
dropping both names leaks both objects, in CPython exactly as here --
which is the whole reason CPython ships a second, tracing mechanism
under this name. `docs/STDLIB.md`'s lifetime section says which
observations are exact; a cycle surviving until `collect()` is one of
the exact ones.

INTERPRETER-ONLY. Finding a cycle means subtracting the references its
members make to each other from their reference COUNTS
(`objects/host.py`'s `_apy_gc_collect` -- CPython's own algorithm),
and only the reference interpreter keeps those counts. A compiled build
refuses BY NAME rather than answering `0`, which would read as "nothing
was collectable" and be false.

NOT COVERED, each refused BY NAME rather than given a plausible-looking
answer: `enable`/`disable` and `set_threshold`/`get_threshold` -- there
are no generations and no automatic runs to enable, disable or tune, so
every one of them would be a knob attached to nothing. `get_objects`,
`get_referrers`, `get_referents`, `get_stats`, `set_debug`,
`garbage`/`DEBUG_*` -- each reports on a generational collector's own
bookkeeping, which this does not have. `freeze`/`unfreeze`,
`is_tracked`, `is_finalized`.
"""


def collect(generation=None):
    """Collect the cycles, and answer how many objects were in them.

    `generation` is accepted and IGNORED, which is the one place this
    module bends its own rule about refusing rather than approximating:
    there is a single pass and no generations, so `collect(0)` and
    `collect(2)` do the same complete job. Refusing the argument would
    break `gc.collect(2)` -- a spelling real code uses to mean "collect
    everything" -- over a distinction that only exists to let CPython do
    LESS work.
    """
    del generation
    return apy_gc_collect()


def isenabled():
    """True, and it means something narrower than CPython's.

    CPython answers whether AUTOMATIC collection is on. There is no
    automatic collection here -- `collect()` runs when called and never
    otherwise -- so what this really reports is that the collector
    exists and works. A program reading it to decide whether it must
    call `collect()` itself gets the answer it needs from
    `docs/STDLIB.md` rather than from here; a program reading it to
    decide whether collection is POSSIBLE gets the right answer.
    """
    return True
