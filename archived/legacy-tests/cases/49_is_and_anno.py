# expect:
# 1
# 1
# 0
# 1
# 7
# 9
# none
# none

# `is` / `is not` lower to integer equality, so they line up with `== None`
# / `!= None` (asmpython's None is the int 0).
print(int(0 is None))
print(int(None is None))
print(int(5 is None))
print(int(5 is not None))

# Variable type annotations: parsed and discarded; the assignment still
# happens (even when bare).
x: int = 7
y: list[int] = [9]
z: str  # bare annotation -- becomes 0
print(x)
print(y[0])
if z is None:
    print("none")

# Aug-assign on `self.x +=` and annotated attr assign.
class Acc:
    def __init__(self):
        self.n: int = 0

    def add(self, m):
        self.n += m

a = Acc()
a.add(5)
a.add(-5)
if a.n is None:
    print("none")
else:
    print("init")
