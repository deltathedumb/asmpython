# COVERAGE: contextmanager over a plain body, over one that suppresses, and
# over one with a `finally`; suppress with one and several exception types and
# with a subclass; ExitStack's callback / push / enter_context / pop_all /
# close and its REVERSE unwinding; nullcontext with and without a value. NOT
# covered here: asynccontextmanager, AsyncExitStack, closing, redirect_stdout,
# chdir, or reusing a manager twice -- the module declares it has none of them.
#
# THE CLEANUP IS THE POINT. Every case records what ran and in what order into
# a list and prints the list, because a manager whose `__exit__` silently does
# not run still produces the right value from the block -- which is exactly
# how this module was wrong once and looked fine.
import contextlib

# ---- contextmanager --------------------------------------------------------
@contextlib.contextmanager
def tracked(log):
    log.append("enter")
    yield log
    log.append("exit")


log = []
with tracked(log) as held:
    held.append("body")
print(log, held is log)

# A SECOND `with` GETS A SECOND GENERATOR, which is why `contextmanager`
# returns a factory rather than an object: reusing one would resume a body
# that had already finished.
first = []
second = []
with tracked(first):
    pass
with tracked(second):
    pass
print(first, second)


# The block's exception reaches the generator AT the yield, so a `try` there
# catches it and swallowing it SUPPRESSES it.
@contextlib.contextmanager
def swallow():
    try:
        yield "v"
    except ValueError:
        pass


with swallow() as v:
    print("got", v)
    raise ValueError("x")
print("suppressed")


# A `finally` runs whether or not the block raised, and the exception still
# propagates -- suppressing needs an `except`, not a `finally`.
@contextlib.contextmanager
def cleaned(log):
    try:
        yield
    finally:
        log.append("cleanup")


log = []
with cleaned(log):
    log.append("body")
print(log)

log = []
try:
    with cleaned(log):
        log.append("body")
        raise ValueError("x")
except ValueError as exc:
    print(log, "propagated", exc)


# An exception the generator raises INSTEAD replaces the block's.
@contextlib.contextmanager
def replace():
    try:
        yield
    except ValueError:
        raise KeyError("instead")


try:
    with replace():
        raise ValueError("x")
except KeyError as exc:
    print("replaced by", type(exc).__name__, exc)


# ---- suppress --------------------------------------------------------------
with contextlib.suppress(ValueError):
    raise ValueError("x")
print("suppress caught it")

with contextlib.suppress(KeyError, ValueError):
    raise KeyError("k")
print("suppress caught the second of two")

# IT MATCHES SUBCLASSES, because `issubclass` is the rule.
with contextlib.suppress(LookupError):
    raise IndexError("i")
print("suppress caught a subclass")

try:
    with contextlib.suppress(KeyError):
        raise ValueError("not this one")
except ValueError:
    print("suppress let the wrong type through")

# Nothing raised at all is not a special case.
with contextlib.suppress(ValueError):
    print("body ran")


# ---- ExitStack -------------------------------------------------------------
# REVERSE ORDER, because a later cleanup may depend on what an earlier one set
# up. Registration order would tear the ground out from under it.
log = []
with contextlib.ExitStack() as stack:
    stack.callback(log.append, "first")
    stack.callback(log.append, "second")
    stack.callback(log.append, "third")
print(log)

log = []
with contextlib.ExitStack() as stack:
    stack.callback(lambda: log.append("no args"))
    stack.callback(log.append, "with an arg")
print(log)


class Noisy:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    def __enter__(self):
        self.log.append("enter " + self.name)
        return self.name

    def __exit__(self, kind, value, traceback):
        self.log.append("exit " + self.name)
        return False


log = []
with contextlib.ExitStack() as stack:
    a = stack.enter_context(Noisy(log, "a"))
    b = stack.enter_context(Noisy(log, "b"))
    log.append("body " + a + b)
print(log)

# `push` TAKES OVER A MANAGER'S EXIT WITHOUT ENTERING IT.
log = []
with contextlib.ExitStack() as stack:
    stack.push(Noisy(log, "pushed"))
    log.append("body")
print(log)

# `pop_all` MOVES the cleanups, so the old stack unwinds nothing.
log = []
with contextlib.ExitStack() as stack:
    stack.callback(log.append, "moved")
    kept = stack.pop_all()
print("after the block:", log)
kept.close()
print("after close:", log)


# ---- nullcontext -----------------------------------------------------------
with contextlib.nullcontext() as nothing:
    print("nullcontext gives", nothing)
with contextlib.nullcontext("a value") as something:
    print("nullcontext gives", something)

# The reason it exists: a conditional `with` needs no branch.
for use_real in (True, False):
    log = []
    manager = cleaned(log) if use_real else contextlib.nullcontext()
    with manager:
        log.append("body")
    print(use_real, log)

print("done")
