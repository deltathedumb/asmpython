# probes: extend mutates while + rebinds
# expect:
# [1, 2]
# [1, 2]
# [1, 2, 3]
base = [1]
alias = base
base.extend([2])
print(alias)
base = base + [3]
print(alias)
print(base)
