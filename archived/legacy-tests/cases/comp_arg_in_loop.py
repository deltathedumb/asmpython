# expect:
# True 0
# True 1
# True 3
# 1
# 1
# 1
for c in range(3):
    print(all([i >= 0 for i in range(c + 1)]), sum(i for i in range(c + 1)))
for c in range(3):
    s = set([1 for i in range(c + 1)])
    print(len(s))
