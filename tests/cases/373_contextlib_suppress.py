# expect:
# before
# after

from contextlib import suppress

with suppress():
    print("before")
    x: int = 1
    print("after")
