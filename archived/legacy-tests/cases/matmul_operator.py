# expect:
# 6
class Mat:
    def __init__(self, v):
        self.v = v
    def __matmul__(self, o):
        return Mat(self.v * o.v)
print((Mat(2) @ Mat(3)).v)
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP ')', got OP '@'
