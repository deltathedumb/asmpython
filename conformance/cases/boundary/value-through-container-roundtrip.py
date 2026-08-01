# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# abc
# True
# 42
# True
# 3.5
# True
# True
# True
# None
# True
for x in ['abc', 42, 3.5, True, None]:
    box = [x]
    print(box[0])
    print(box[0] == x)
