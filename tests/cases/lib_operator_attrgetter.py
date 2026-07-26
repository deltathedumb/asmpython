# expect:
# [1, 2, 3]
from operator import attrgetter
class P:
    def __init__(self, n):
        self.n = n
ps = [P(3), P(1), P(2)]
print([p.n for p in sorted(ps, key=attrgetter('n'))])
# asmpython (beta/3.14.0) rejects at compile: [E135] key= must be a lambda literal or a name bound to a lambda
