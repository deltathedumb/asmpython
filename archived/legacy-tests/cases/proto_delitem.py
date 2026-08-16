# probes: __delitem__ serves del on a subscript
# expect:
# 1
class Store:
    def __init__(self):
        self.data = {"a": 1, "b": 2}

    def __delitem__(self, key):
        del self.data[key]

    def __len__(self):
        return len(self.data)


s = Store()
del s["a"]
print(len(s))
