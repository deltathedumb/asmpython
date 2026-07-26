# expect:
# 3.0
class N:
    def __init__(self, v):
        self.v = v
    def __float__(self):
        return float(self.v)
print(float(N(3)))
# asmpython (beta/3.14.0) rejects at compile: [E022] float() requires str / int / float
