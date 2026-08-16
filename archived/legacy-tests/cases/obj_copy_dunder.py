# probes: __copy__ customises copy.copy
# expect:
# copied
import copy


class Tagged:
    def __copy__(self):
        clone = Tagged()
        clone.tag = "copied"
        return clone


print(copy.copy(Tagged()).tag)
