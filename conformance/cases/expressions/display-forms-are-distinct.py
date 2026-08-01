# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# list tuple dict
# set set
# generator
# list
# set
# dict
print(type([]).__name__, type(()).__name__, type({}).__name__)
print(type({1}).__name__, type(set()).__name__)
print(type((v for v in [])).__name__)
print(type([v for v in []]).__name__)
print(type({v for v in []}).__name__)
print(type({v: v for v in []}).__name__)
