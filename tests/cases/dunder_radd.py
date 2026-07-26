# expect:
# 15
class Money:
    def __init__(self, v):
        self.v = v
    def __radd__(self, other):
        return Money(self.v + other)
m = 10 + Money(5)
print(m.v)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
