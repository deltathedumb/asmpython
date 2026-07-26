# expect:
# [1, 2, 3]
class Builder:
    def __init__(self):
        self.parts = []
    def add(self, x):
        self.parts.append(x)
        return self
b = Builder().add(1).add(2).add(3)
print(b.parts)
# asmpython (beta/3.14.0) MISMATCH: prints '[8492528, 8492560, 8492464]\n' (wrong).
