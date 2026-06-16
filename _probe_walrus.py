# expect:
# 10
# found: 4
# [4, 16]

x: int = 0
if (x := 10) > 5:
    print(x)

nums = [1, 2, 3, 4, 5]
for n in nums:
    if (sq := n * n) > 9:
        print("found: " + str(n))
        break

evens = [y for x in range(1, 6) if (y := x * x) % 2 == 0]
print(evens)
