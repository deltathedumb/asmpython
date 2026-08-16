# expect:
# 10 20
# 30 40
# 4
# 6

# `for a, b in pairs`: tuple-unpacking targets in a for loop. Each element is a
# pair (list or tuple); its two slots bind to the two targets. Per-slot element
# kinds of an opaque iterable aren't tracked, so the targets are opaque — use
# them as int/pointer slots (the binding itself is what's exercised here).

rows = [[10, 20], [30, 40]]
for x, y in rows:
    print(x, y)

# Unpacked values flow into arithmetic correctly (opaque slots hold the raw
# 8-byte value, which is the int for int elements).
sums = [(1, 3), (2, 4)]
for a, b in sums:
    print(a + b)
