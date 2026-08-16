# expect:
# 1
# 0
# 1
# 0

d = {"alice": 30, "bob": 25}
print(int("alice" in d))
print(int("dave" in d))
print(int("dave" not in d))
print(int("alice" not in d))
