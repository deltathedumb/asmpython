# expect:
# 21
# 28
# 36
# 121

# Functions with more parameters than the ABI's argument registers (4 on
# Win64, 6 on SysV) pass the extras on the stack. Exercise 6, 7, and 8
# parameters, plus a method (where `self` counts as the first argument) so the
# 7-slot method overflows on both targets.

def add6(a, b, c, d, e, f):
    return a + b + c + d + e + f

def add7(a, b, c, d, e, f, g):
    return a + b + c + d + e + f + g

def add8(a, b, c, d, e, f, g, h):
    return a + b + c + d + e + f + g + h

print(add6(1, 2, 3, 4, 5, 6))        # 21
print(add7(1, 2, 3, 4, 5, 6, 7))     # 28
print(add8(1, 2, 3, 4, 5, 6, 7, 8))  # 36


class Accum:
    def __init__(self, base):
        self.base = base

    # self + 6 params = 7 args; overflows registers on both targets.
    def total(self, a, b, c, d, e, f):
        return self.base + a + b + c + d + e + f


acc = Accum(100)
print(acc.total(1, 2, 3, 4, 5, 6))   # 121
