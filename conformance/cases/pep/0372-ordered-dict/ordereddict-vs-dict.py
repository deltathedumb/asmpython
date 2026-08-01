# tier: spec
# ref: library/collections.html#collections.OrderedDict
# expect:
# ['a', 'b']
# ['b', 'a']
# False
# True
from collections import OrderedDict

od = OrderedDict([("a", 1), ("b", 2)])
print(list(od))
od.move_to_end("a")
print(list(od))
print(OrderedDict([("a", 1), ("b", 2)]) == OrderedDict([("b", 2), ("a", 1)]))
print({"a": 1, "b": 2} == {"b": 2, "a": 1})
