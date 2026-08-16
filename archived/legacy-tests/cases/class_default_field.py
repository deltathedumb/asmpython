# expect:
# [1, 2]
class C:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)
c = C()
c.add(1)
c.add(2)
print(c.items)
# asmpython (beta/3.14.0) MISMATCH: prints '[9803248, 9803280]\n' (wrong).
