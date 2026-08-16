# expect:
# 1
# 0
# 1
# 0

names = ["alice", "bob", "carol"]
print(int("bob" in names))
print(int("dave" in names))
print(int("dave" not in names))
print(int("alice" not in names))
