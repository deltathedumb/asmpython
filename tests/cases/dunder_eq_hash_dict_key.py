# expect:
# 1
class K:
    def __init__(self, v):
        self.v = v
    def __hash__(self):
        return hash(self.v)
    def __eq__(self, o):
        return self.v == o.v
d = {K('a'): 1}
print(d[K('a')])
# asmpython (beta/3.14.0) runtime failure: exit 0x1
