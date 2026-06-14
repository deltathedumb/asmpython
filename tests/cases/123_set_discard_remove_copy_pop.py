# expect:
# 2
# False
# 2
# 1
# False
# False
# True
# 1
# 2
# x
# 0

s = {"a", "b", "c"}
s.discard("b")
print(len(s))
print("b" in s)

s.discard("zzz")
print(len(s))

s.remove("a")
print(len(s))
print("a" in s)

s2 = s.copy()
s2.add("z")
print("z" in s)
print("z" in s2)
print(len(s))
print(len(s2))

t = {"x"}
y = t.pop()
print(y)
print(len(t))
