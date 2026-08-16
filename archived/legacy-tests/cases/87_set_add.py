# expect:
# False
# True
# True
# False
# True

# set.add(x): insert a member into a set built at runtime. Sets are dict-backed,
# so add maps onto a dict insert and membership stays a str-key lookup.

s = set()
print("a" in s)   # False (empty)
s.add("a")
s.add("b")
print("a" in s)   # True
print("b" in s)   # True
print("c" in s)   # False
s.add("c")
print("c" in s)   # True
