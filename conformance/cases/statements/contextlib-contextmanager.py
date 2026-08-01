# tier: spec
# ref: library/contextlib.html#contextlib.contextmanager
# expect:
# [('enter', 1), ('body', 2), ('exit', 1)]
# [('exit', 2), 'caught']
import contextlib

log = []

@contextlib.contextmanager
def managed(n):
    log.append(("enter", n))
    try:
        yield n * 2
    finally:
        log.append(("exit", n))

with managed(1) as v:
    log.append(("body", v))
print(log)

try:
    with managed(2):
        raise ValueError("x")
except ValueError:
    log.append("caught")
print(log[-2:])
