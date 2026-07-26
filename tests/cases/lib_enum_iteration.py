# expect:
# ['A', 'B', 'C']
from enum import Enum
class C(Enum):
    A = 1
    B = 2
    C = 3
print([e.name for e in C])
# asmpython (beta/3.14.0) rejects at compile: [E018] cannot iterate a type in a comprehension
