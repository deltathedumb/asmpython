# tier: spec
# ref: library/functions.html#breakpoint
# expect:
# [((), []), ((1,), ['k'])]
# True
import sys

calls = []
sys.breakpointhook = lambda *a, **kw: calls.append((a, sorted(kw)))
breakpoint()
breakpoint(1, k=2)
print(calls)
print(callable(breakpoint))
