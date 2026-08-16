# guards: iterable_element_compat_fixes
# expect:
# ADA
# 4
def first_of(seq):
    for item in seq:
        return item
    return None


def names():
    return ["ada", "bob"]


picked = first_of(names())
print(picked.upper())

nums = [3, 1, 2]
print(first_of(nums) + 1)
