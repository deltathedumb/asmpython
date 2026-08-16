# expect:
# [(1, 2), (2, 1), (3, 3), (1, 1)]
nums = [1, 1, 2, 3, 3, 3, 1]
groups = []
for n in nums:
    if groups and groups[-1][0] == n:
        groups[-1] = (n, groups[-1][1] + 1)
    else:
        groups.append((n, 1))
print(groups)
# asmpython (beta/3.14.0) rejects at compile: [E017] cannot index a ?
