# Regression: str(int) must return a fresh copy, not the shared itoa buffer,
# so stored results don't all alias the last conversion.
# expect:
# 1
# 2
# 3
# 1, 2, 3
# 10|20
xs = [1, 2, 3]
strs = [str(x) for x in xs]
print(strs[0])
print(strs[1])
print(strs[2])
print(", ".join([str(x) for x in xs]))
a = str(10)
b = str(20)
print(a + "|" + b)
