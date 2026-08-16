# expect:
# 5
# 5
# 15
# hello
# 5
# 4.5
# 3.5
# True
# True
# None
# None
# [2, 4, 6]
# 6
# 25

# The walrus operator `:=` (assignment expression): evaluates the RHS,
# binds it to the name, and the whole expression yields that value.

data = [1, 2, 3, 4, 5]
if (n := len(data)) > 3:
    print(n)
print(n)

total = 0
i = 0
while (i := i + 1) <= 5:
    total += i
print(total)

s = "hello"
print(s if (length := len(s)) > 3 else "short")
print(length)

x = 0.0
print((x := 3.5) + 1.0)
print(x)

print((b := (2 > 1)))
print(b)

print((m := None))
print(m)

# A walrus target inside a comprehension binds in the enclosing scope (PEP
# 572), unlike the comprehension's own loop variable.
vals = [1, 2, 3]
results = [y := v * 2 for v in vals]
print(results)
print(y)


def f(xs: list[int]) -> int:
    total2 = 0
    for x in xs:
        if (sq := x * x) > 4:
            total2 += sq
    return total2


print(f([1, 2, 3, 4]))
