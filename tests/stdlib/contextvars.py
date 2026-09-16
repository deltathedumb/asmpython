# COVERAGE: ContextVar construction (name, keyword-only default=), .name,
# .get() with no argument (constructor default, then LookupError when there
# is truly nothing), .get(default) overriding for one call without changing
# what a later plain .get() sees, .set()/.reset(token) as an exact round
# trip including Token.MISSING when a set() had nothing to restore, a token
# refused when reused (RuntimeError) or used against the wrong ContextVar
# (ValueError) or the wrong Context (ValueError), Context.run() isolating
# what it does from the caller (and returning the callable's result /
# propagating its exception), Context.run() refusing to re-enter itself
# (RuntimeError), copy_context() carrying forward exactly what is visible
# and nothing set afterward, a brand-new Context() starting genuinely empty
# rather than inheriting anything, and the mapping protocol on Context
# (__contains__, __len__, __iter__, keys, values, items, __getitem__ raising
# KeyError, get() with its own default that does NOT consult the
# ContextVar's constructor default).
#
# NOT COVERED, and deliberately absent from this file rather than left
# failing: per-asyncio-task context isolation. CPython's real Task copies
# the current context when it is created and confines each step to that
# copy, so a plain `asyncio.run(coro())` already gives a single coroutine
# its own private context -- a ContextVar it .set() does not leak back to
# the caller once asyncio.run() returns. uasm's scheduler drives a
# task's generator directly with no such copy (see the module's own
# docstring, and `src/uasm/runtime/tasks.py`), so the identical
# program run under uasm DOES leak that .set() back out. This was
# checked, not assumed: a scratch probe of exactly that shape --
#
#     v.set("before-run")
#     async def worker():
#         v.set("inside-task")
#         await asyncio.sleep(0)
#     asyncio.run(worker())
#     print(v.get())
#
# -- prints "before-run" under CPython and "inside-task" under uasm.
# Including it here would fail the byte-identical requirement for a gap in
# the async runtime that this module cannot close on its own, so this file
# stays synchronous throughout and the gap is named here instead.
import contextvars


def snapshot(ctx):
    """`ctx`'s content as {name: value}, so printing never depends on the
    order a Context happens to iterate in -- which even CPython does not
    promise, and which nothing here should be seen depending on."""
    return {var.name: value for var, value in ctx.items()}


# ---- construction, .name, defaults, LookupError ----------------------------
a = contextvars.ContextVar("a")
print(a.name)

try:
    a.get()
    print("WRONG: no LookupError")
except LookupError:
    print("LookupError: unset, no default")

# A CALL-TIME DEFAULT IS FOR THAT CALL ONLY.
print(a.get("fallback"))
try:
    a.get()
    print("WRONG: no LookupError")
except LookupError:
    print("LookupError: call-time default did not stick")

b = contextvars.ContextVar("b", default="D")
print(b.get())
print(b.get("override"))
print(b.name)


# ---- set / get / reset, and Token.MISSING ----------------------------------
c = contextvars.ContextVar("c")
t1 = c.set(1)
print(c.get())
print(t1.old_value is contextvars.Token.MISSING)
print(t1.var is c)

t2 = c.set(2)
print(c.get())
print(t2.old_value)

c.reset(t2)
print(c.get())

c.reset(t1)
try:
    c.get()
    print("WRONG: no LookupError")
except LookupError:
    print("LookupError: reset all the way back to unset")


# ---- token misuse: reused, wrong ContextVar, wrong Context -----------------
d = contextvars.ContextVar("d", default=0)
t3 = d.set(5)
d.reset(t3)
try:
    d.reset(t3)
    print("WRONG: no RuntimeError")
except RuntimeError:
    print("RuntimeError: token already used")

e = contextvars.ContextVar("e")
te = e.set(9)
try:
    d.reset(te)
    print("WRONG: no ValueError")
except ValueError:
    print("ValueError: token belongs to a different ContextVar")
e.reset(te)

f = contextvars.ContextVar("f", default="root")
tf = f.set("outer")


def reset_elsewhere():
    try:
        f.reset(tf)
        print("WRONG: no ValueError")
    except ValueError:
        print("ValueError: token from a different Context")


contextvars.Context().run(reset_elsewhere)
f.reset(tf)  # succeeds back in the context the set() actually happened in
print(f.get())


# ---- Context.run: isolation, return value, propagated exception, reentrancy
g = contextvars.ContextVar("g", default="ambient")
g.set("before")


def inside():
    print("copy sees inherited:", g.get())
    g.set("mutated-in-copy")
    print("copy sees its own mutation:", g.get())
    return "inside-result"


snap = contextvars.copy_context()
print("run() returns:", snap.run(inside))
print("caller unaffected by the copy's mutation:", g.get())

fresh = contextvars.Context()


def sees_fresh():
    # A BRAND NEW Context() INHERITS NOTHING, so this falls back to the
    # ContextVar's own constructor default rather than seeing "before".
    print("fresh empty Context sees:", g.get())


fresh.run(sees_fresh)
print("caller still:", g.get())


def boom():
    raise ValueError("kaboom")


ctx_boom = contextvars.Context()
try:
    ctx_boom.run(boom)
    print("WRONG: no exception propagated")
except ValueError as exc:
    print("propagated:", type(exc).__name__, str(exc))

ctx_re = contextvars.Context()


def reenter():
    return ctx_re.run(sees_fresh)


try:
    ctx_re.run(reenter)
    print("WRONG: no RuntimeError")
except RuntimeError:
    print("RuntimeError: cannot re-enter a Context already running")

# NESTING TWO DIFFERENT CONTEXTS IS FINE -- only re-entering the SAME one
# is refused.
outer_ctx = contextvars.Context()
inner_ctx = contextvars.Context()


def run_inner():
    return inner_ctx.run(lambda: "inner-ran")


print(outer_ctx.run(run_inner))


# ---- Context as a mapping ---------------------------------------------------
h = contextvars.ContextVar("h", default=0)
i = contextvars.ContextVar("i")

empty = contextvars.Context()
print(len(empty), list(empty), h in empty)
print(empty.get(h), empty.get(h, "default-arg"))
try:
    empty[h]
    print("WRONG: no KeyError")
except KeyError:
    print("KeyError: h not present in an empty Context")

populated = contextvars.Context()


def populate():
    h.set(10)
    i.set("hi")


populated.run(populate)
print(len(populated))
print(h in populated, i in populated)
print(sorted(var.name for var in populated))        # __iter__ yields ContextVars
print(sorted(var.name for var in populated.keys()))  # keys() too
for name in sorted(snapshot(populated)):
    print(name, snapshot(populated)[name])
print(populated[h], populated[i])
print(populated.get(h), populated.get(i))

j = contextvars.ContextVar("j", default=99)
# Context.get() DOES NOT consult the ContextVar's own default -- it is a
# plain mapping get() with a plain default of its own.
print(populated.get(j))
print(populated.get(j, "explicit-fallback"))
print(j in populated)

# NOTE: CPython also refuses `Context(populated)` -- the constructor takes
# no arguments -- with a TypeError. That is not exercised here: it would
# depend on positional-argument arity being enforced at all for a
# user-defined callable, which this compiler does not yet do for ANY
# function, bound method or constructor (a plain `def f(x): ...` called
# `f(1, 2)` is accepted at run time here, only warned about at compile
# time) -- a general, pre-existing gap with nothing specific to
# `contextvars` about it, so it is left for whichever module's coverage
# line first needs it rather than mixed into this one.
try:
    a.get("one", "two")
    print("WRONG: no TypeError")
except TypeError:
    print("TypeError: get() takes at most one argument")

print("done")
