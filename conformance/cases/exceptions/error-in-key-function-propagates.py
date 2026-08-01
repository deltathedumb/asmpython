# tier: spec
# ref: library/functions.html#sorted
# expect:
# ValueError
# ValueError
# [1, 3]
def bad_key(v):
    if v == 2:
        raise ValueError("bad")
    return v

try:
    sorted([1, 2, 3], key=bad_key)
except ValueError:
    print("ValueError")

try:
    max([1, 2], key=bad_key)
except ValueError:
    print("ValueError")
print(sorted([3, 1], key=lambda v: v))
