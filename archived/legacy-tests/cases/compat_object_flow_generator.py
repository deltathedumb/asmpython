# guards: object_flow_compat_fixes
# expect:
# 2
# 4
# 6
class Tree:
    def __init__(self, values):
        self.values = values

    def walk(self):
        for v in self.values:
            yield v * 2


t = Tree([1, 2, 3])
for v in t.walk():
    print(v)
