# probes: *args collects the extra positionals
# expect:
# 1
# 6
def total(first, *rest):
    return first + sum(rest)


print(total(1))
print(total(1, 2, 3))
