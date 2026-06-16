# probe: more patterns

# 1. Walrus operator :=
xs = [1, 2, 3, 4, 5, 6]
if (n := len(xs)) > 4:
    print(n)

# 2. while with else
i = 0
while i < 3:
    i += 1
else:
    print(i)

# 3. for with else
found = False
for x in [1, 2, 3]:
    if x == 2:
        found = True
        break
else:
    print("not found")
print(found)

# 4. try/except/else
def safe(n: int) -> str:
    try:
        result = 10 // n
    except ZeroDivisionError:
        return "zero"
    else:
        return "ok"

print(safe(2))
print(safe(0))

# 5. Chained comparisons
x = 5
print(1 < x < 10)
print(x < 3 or x > 8)
