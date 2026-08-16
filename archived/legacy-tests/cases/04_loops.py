# expect:
# 55
# 5
# 4
# 3
# 2
# 1
# 1
# 3
# 5
# 7
# 9
total = 0
for i in range(1, 11):
    total += i
print(total)

for i in range(5, 0, -1):
    print(i)

for i in range(1, 11):
    if i % 2 == 0:
        continue
    print(i)
