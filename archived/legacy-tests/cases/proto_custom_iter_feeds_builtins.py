# probes: a custom iterable feeds sum/list/max
# expect:
# 6
# [1, 2, 3]
# 3
class Three:
    def __iter__(self):
        return iter([3, 1, 2])


print(sum(Three()))
print(sorted(Three()))
print(max(Three()))
