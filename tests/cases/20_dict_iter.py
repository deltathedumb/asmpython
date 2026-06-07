# expect:
# total = 6
d = {"a": 1, "b": 2, "c": 3}
total = 0
for k in d:
    total += d[k]
print("total", "=", total)
