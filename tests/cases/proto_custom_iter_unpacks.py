# probes: a custom iterable unpacks into names
# expect:
# 10
# 20
class Pair:
    def __iter__(self):
        return iter([10, 20])


left, right = Pair()
print(left)
print(right)
