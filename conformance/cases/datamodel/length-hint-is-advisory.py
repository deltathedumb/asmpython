# tier: impl
# ref: library/operator.html#operator.length_hint
# expect:
# 5
# 2
# 3
import operator

class C:
    def __length_hint__(self):
        return 5

print(operator.length_hint(C()))
print(operator.length_hint([1, 2]))
print(operator.length_hint(iter([1, 2, 3])))
