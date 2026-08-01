# tier: spec
# ref: library/contextlib.html#contextlib.suppress
# expect:
# continued
# [3, 2, 1]
import contextlib

with contextlib.suppress(ValueError):
    raise ValueError("swallowed")
print("continued")

log = []
with contextlib.ExitStack() as stack:
    for n in (1, 2, 3):
        stack.callback(log.append, n)
print(log)
