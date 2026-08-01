# tier: spec
# ref: library/functions.html#super
# expect:
# ['A', 'B', 'C']
class A:
    def __init__(self):
        self.trail = ["A"]

class B(A):
    def __init__(self):
        super().__init__()
        self.trail.append("B")

class C(B):
    def __init__(self):
        super().__init__()
        self.trail.append("C")

print(C().trail)
