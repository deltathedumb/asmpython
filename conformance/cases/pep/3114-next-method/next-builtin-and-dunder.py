# tier: spec
# ref: library/functions.html#next
# expect:
# 1
# 1
# False
# default
class It:
    def __iter__(self):
        return self
    def __next__(self):
        return 1

i = It()
print(next(i))
print(i.__next__())
print(hasattr(i, "next"))
print(next(iter([]), "default"))
