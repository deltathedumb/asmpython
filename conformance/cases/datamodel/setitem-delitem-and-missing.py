# tier: spec
# ref: reference/datamodel.html#object.__setitem__
# expect:
# 1 1
# 0
# default:nope
class Store:
    def __init__(self):
        self.d = {}
    def __setitem__(self, k, v):
        self.d[k] = v
    def __getitem__(self, k):
        return self.d[k]
    def __delitem__(self, k):
        del self.d[k]
    def __len__(self):
        return len(self.d)

s = Store()
s["a"] = 1
print(s["a"], len(s))
del s["a"]
print(len(s))

class WithMissing(dict):
    def __missing__(self, k):
        return "default:" + k

w = WithMissing()
print(w["nope"])
