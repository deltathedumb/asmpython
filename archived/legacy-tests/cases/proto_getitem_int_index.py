# probes: __getitem__ serves integer subscription
# expect:
# 9
# 25
class Squares:
    def __getitem__(self, index):
        return index * index


s = Squares()
print(s[3])
print(s[5])
