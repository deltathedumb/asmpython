# expect:
# wrapped
# partial

from functools import update_wrapper, partialmethod

def inner() -> str:
    return "inner"

result: int = update_wrapper(1, 0)
print("wrapped")

pm: int = partialmethod(0, 1)
print("partial")
