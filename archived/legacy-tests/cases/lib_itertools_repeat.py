# expect:
# ['x', 'x', 'x']
from itertools import repeat
print(list(repeat('x', 3)))
# asmpython (beta/3.14.0) MISMATCH: prints '[5368737834, 5368737834, 5368737834]\n' (wrong).
