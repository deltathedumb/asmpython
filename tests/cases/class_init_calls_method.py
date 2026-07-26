# expect:
# [0, 1, 2]
class C:
    def __init__(self, n):
        self.vals = []
        self.fill(n)
    def fill(self, n):
        for i in range(n):
            self.vals.append(i)
print(C(3).vals)
# asmpython (beta/3.14.0) MISMATCH: prints '[9934320, 9934352, 9934256]\n' (wrong).
