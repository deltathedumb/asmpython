# probe74: while/else, for/else, break/continue with nested loops
# for/else
found = False
for i in range(10):
    if i == 5:
        found = True
        break
else:
    print("not found")

print(found)   # True

for i in range(10):
    if i == 99:
        break
else:
    print("exhausted")   # exhausted

# while/else
n: int = 5
while n > 0:
    n = n - 1
else:
    print("done")   # done

# Nested break
found2 = False
for i in range(5):
    for j in range(5):
        if i == 2 and j == 3:
            found2 = True
            break
    if found2:
        break

print(found2)   # True
print(i)        # 2
print(j)        # 3

# continue
total: int = 0
for i in range(10):
    if i % 2 == 0:
        continue
    total = total + i
print(total)   # 25 (1+3+5+7+9)
