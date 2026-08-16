# probes: __setitem__ serves subscript assignment
# expect:
# 7
class Store:
    def __init__(self):
        self.data = {}

    def __setitem__(self, key, value):
        self.data[key] = value

    def __getitem__(self, key):
        return self.data[key]


s = Store()
s["k"] = 7
print(s["k"])
