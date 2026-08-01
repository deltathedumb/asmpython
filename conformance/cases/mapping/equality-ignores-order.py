# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# True
# True
# True
# TypeError
print({"a": 1, "b": 2} == {"b": 2, "a": 1})
print({"a": 1} == {"a": 1.0})
print({"a": 1} != {"a": 2})
try:
    {"a": 1} < {"b": 2}
except TypeError:
    print("TypeError")
