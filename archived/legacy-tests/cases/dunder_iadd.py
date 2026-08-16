# expect:
# 8
class Acc:
    def __init__(self):
        self.total = 0
    def __iadd__(self, x):
        self.total += x
        return self
a = Acc()
a += 5
a += 3
print(a.total)
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
