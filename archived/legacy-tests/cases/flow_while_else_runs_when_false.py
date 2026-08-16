# probes: while/else runs when the condition fails
# expect:
# condition ended it
# 2
n = 0
while n < 2:
    n = n + 1
else:
    print("condition ended it")
print(n)
