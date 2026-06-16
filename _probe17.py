# probe17: various patterns
# 1. while/else
i = 0
while i < 3:
    i += 1
else:
    print("done")

# 2. for/else
for x in [1, 2, 3]:
    pass
else:
    print("for_done")

# 3. chained comparisons
x = 5
print(1 < x < 10)
print(1 < x < 4)

# 4. ternary
v = 1 if x > 3 else 0
print(v)

# 5. augmented assign on list element
xs = [0, 1, 2]
xs[1] += 10
print(xs[1])

# 6. walrus operator
if (n := len([1, 2, 3])) > 2:
    print(n)
