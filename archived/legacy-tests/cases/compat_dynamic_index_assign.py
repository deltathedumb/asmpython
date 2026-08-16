# guards: dynamic_index_assignment_compat_fixes
# expect:
# 1
# 2
# 2
def store(target, key, value):
    target[key] = value
    return target[key]


bag = {}
print(store(bag, "alpha", 1))
print(store(bag, "beta", 2))
print(len(bag))
