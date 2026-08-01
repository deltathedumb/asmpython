# probes: __matmul__ serves the @ operator
# expect:
# a@b
class Mat:
    def __init__(self, tag):
        self.tag = tag

    def __matmul__(self, other):
        return Mat(self.tag + "@" + other.tag)


print((Mat("a") @ Mat("b")).tag)
