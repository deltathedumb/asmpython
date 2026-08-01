# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# TypeError
# False
# True
# TypeError
# True
try:
    1 < "a"
except TypeError:
    print("TypeError")
print(1 == "a")
print(1 != "a")
try:
    None < None
except TypeError:
    print("TypeError")
print(None == None)
