# tier: spec
# ref: reference/datamodel.html#object.__call__
# expect:
# 15
# True True False
class Adder:
    def __init__(self, n):
        self.n = n
    def __call__(self, v):
        return self.n + v

a = Adder(10)
print(a(5))
print(callable(a), callable(Adder), callable(1))
