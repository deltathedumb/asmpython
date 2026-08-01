# tier: spec
# ref: reference/compound_stmts.html#the-for-statement
# expect:
# [1, 2]
# loop-ended-cleanly
# StopIteration-when-explicit
class Iter:
    def __init__(self):
        self.n = 0
    def __iter__(self):
        return self
    def __next__(self):
        self.n += 1
        if self.n > 2:
            raise StopIteration
        return self.n

print([v for v in Iter()])
print("loop-ended-cleanly")
try:
    next(iter([]))
except StopIteration:
    print("StopIteration-when-explicit")
