pairs = [("a", 3), ("b", 1), ("c", 2)]
# Without min, just access by index
x = pairs[1]
print(x)      # ('b', 1)
print(x[0])   # b
print(x[1])   # 1

# Now use min without key (default behavior)
# m = min(pairs)  # would fail because str/int mixed
