# tier: spec
# ref: library/typing.html#typing.LiteralString
# min-python: 3.11
# expect:
# ok
# True
from typing import LiteralString

def q(s: LiteralString) -> str:
    return s

print(q("ok"))
print(LiteralString.__class__.__name__ != "")
