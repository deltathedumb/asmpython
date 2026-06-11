# expect:
# 0
# 1
# 1
# 0
# 1

# set.add(x): insert a member into a set built at runtime. Sets are dict-backed,
# so add maps onto a dict insert and membership stays a str-key lookup.

s = set()
print("a" in s)   # 0  (empty)
s.add("a")
s.add("b")
print("a" in s)   # 1
print("b" in s)   # 1
print("c" in s)   # 0
s.add("c")
print("c" in s)   # 1
