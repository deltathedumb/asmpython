total = 0
for i in range(1, 11):
    total += i
print("sum 1..10 =")
print(total)

print("countdown:")
for i in range(5, 0, -1):
    print(i)

print("first even >= 7:")
i = 7
while i < 100:
    if i % 2 == 0:
        print(i)
        break
    i += 1

print("skip evens 1..10:")
for i in range(1, 11):
    if i % 2 == 0:
        continue
    print(i)

print("chained: 0 < 5 < 10 ->")
print(0 < 5 < 10)
print("chained: 0 < 5 < 3 ->")
print(0 < 5 < 3)

print("bitwise:")
print(0b1100 & 0b1010)
print(0b1100 | 0b1010)
print(0b1100 ^ 0b1010)
print(1 << 4)
print(256 >> 3)
print(~0)
