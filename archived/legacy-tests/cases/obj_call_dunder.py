# probes: __call__ makes an instance callable
# expect:
# 8
# True
class Adder:
    def __init__(self, base):
        self.base = base

    def __call__(self, n):
        return self.base + n


add5 = Adder(5)
print(add5(3))
print(callable(add5))
