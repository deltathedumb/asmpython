# probes: str supports * and join
# expect:
# ababab
# a-b-c
print("ab" * 3)
print("-".join(["a", "b", "c"]))
