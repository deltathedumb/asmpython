# guards: ordered_flow_compat_fixes
# expect:
# leaf:b
# 2
class Leaf:
    def __init__(self, tag):
        self.tag = tag

    def show(self):
        return "leaf:" + self.tag


class Branch:
    def __init__(self):
        self.leaves = []

    def add(self, leaf):
        self.leaves.append(leaf)
        return self

    def newest(self):
        return self.leaves[len(self.leaves) - 1]


b = Branch()
b.add(Leaf("a")).add(Leaf("b"))
print(b.newest().show())
print(len(b.leaves))
