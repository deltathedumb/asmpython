# tier: spec
# ref: reference/datamodel.html#emulating-numeric-types
# expect:
# add
# sub
# mul
# truediv
# floordiv
# mod
# pow
# matmul
class N:
    def __init__(self, v):
        self.v = v
    def __add__(self, o): return "add"
    def __sub__(self, o): return "sub"
    def __mul__(self, o): return "mul"
    def __truediv__(self, o): return "truediv"
    def __floordiv__(self, o): return "floordiv"
    def __mod__(self, o): return "mod"
    def __pow__(self, o): return "pow"
    def __matmul__(self, o): return "matmul"

n = N(1)
for op in (n + 1, n - 1, n * 1, n / 1, n // 1, n % 1, n ** 1, n @ 1):
    print(op)
