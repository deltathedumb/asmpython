# expect:
# [1, 2]
# [10, 20]
# [5, 5]

# The instance-iterable comprehension path, i.e. iterating a user class with
# __iter__/__next__. Same bug as 306 and the same cause -- the shadow set was
# pushed after the target stores -- but a fourth, separate lowering function, so
# it needs its own case: `k = 99; [k for k, v in Pairs()]` gave [0, 0] instead
# of [1, 2].
#
# The third line pins the OTHER direction: a name that is NOT a comprehension
# target must still read the module global inside the body. Over-shadowing
# would break that, and nothing else here would notice.
class Pairs:
    def __init__(self):
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i >= 2:
            raise StopIteration
        self.i = self.i + 1
        return (self.i, self.i * 10)


k = 99
v = 98
scale = 5

print([k for k, v in Pairs()])
print([v for k, v in Pairs()])
print([scale for k, v in Pairs()])
