# tier: spec
# ref: peps.python.org/pep-0572/
# expect:
# 4
# [5]
# 5
# 6
xs = [1, 2, 3, 4]
if (n := len(xs)) > 3:
    print(n)
print([y := 5])
print(y)
total = 0
vals = [1, 2, 3]
i = 0
while (v := vals[i] if i < len(vals) else None) is not None:
    total += v
    i += 1
print(total)
