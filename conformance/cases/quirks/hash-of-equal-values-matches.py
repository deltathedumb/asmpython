# tier: spec
# ref: library/functions.html#hash
# expect:
# True
# True
# True
# True
print(hash(1) == hash(1.0))
print(hash((1, 2)) == hash((1, 2)))
print(hash("a") == hash("a"))
print(hash(True) == hash(1))
