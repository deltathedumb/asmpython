def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)

print("factorials:")
i = 1
while i <= 8:
    print(fact(i))
    i = i + 1

print("negatives + modulo:")
print(-42)
print(17 // 5)
print(17 % 5)

print("booleans (1=True, 0=False):")
print(3 < 5 and 10 > 2)
print(3 < 5 and 10 < 2)
print(3 > 5 or 10 > 2)
