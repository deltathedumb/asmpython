# probes: while/else is skipped after a break
# expect:
# 2
n = 0
while True:
    n = n + 1
    if n == 2:
        break
else:
    print("not reached")
print(n)
