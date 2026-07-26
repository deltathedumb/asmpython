# expect:
# True False
from dataclasses import dataclass
@dataclass
class P:
    x: int
    y: int
print(P(1, 2) == P(1, 2), P(1, 2) == P(1, 3))
# asmpython (beta/3.14.0) MISMATCH: prints 'False False\n' (wrong).
