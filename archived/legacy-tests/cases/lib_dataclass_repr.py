# expect:
# P(x=1, y=2)
from dataclasses import dataclass
@dataclass
class P:
    x: int
    y: int
print(repr(P(1, 2)))
# asmpython (beta/3.14.0) MISMATCH: prints '10000848\n' (wrong).
