# probes: a generator expression works as a sole argument
# expect:
# 3
print(max(len(w) for w in ["a", "abc", "ab"]))
