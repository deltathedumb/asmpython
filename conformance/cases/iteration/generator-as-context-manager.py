# tier: spec
# ref: library/contextlib.html#contextlib.contextmanager
# expect:
# ['enter', 'body', 'exit']
# suppressed
import contextlib

@contextlib.contextmanager
def tracked(log):
    log.append("enter")
    yield log
    log.append("exit")

log = []
with tracked(log) as l:
    l.append("body")
print(log)

@contextlib.contextmanager
def swallow():
    try:
        yield
    except ValueError:
        pass

with swallow():
    raise ValueError("x")
print("suppressed")
