pairs = [("a", 3), ("b", 1), ("c", 2)]
m = min(pairs, key=lambda p: p[1])
print(m)      # expect ('b', 1)
print(m[0])   # expect b
print(m[1])   # expect 1
