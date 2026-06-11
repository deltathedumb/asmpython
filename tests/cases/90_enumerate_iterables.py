# expect:
# 0 a
# 1 b
# 2 c
# 0 10
# 1 20
# 0 x
# 1 y
# 2 z

# enumerate() over a tuple, a string, and an opaque (dict-value) list — all
# share or adapt the list-scan path. (Previously only list was supported.)

t = ("a", "b", "c")
for i, x in enumerate(t):
    print(i, x)

# enumerate over a list-typed value read out of an opaque container
box = {"nums": [10, 20]}
nums = box["nums"]              # typed opaque
for i, n in enumerate(nums):
    print(i, n)

for i, ch in enumerate("xyz"):
    print(i, ch)
