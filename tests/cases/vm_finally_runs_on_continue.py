# probes: finally executes on the continue path
# expect:
# finally 1
# finally 2
# finally 3
# 4
def f():
    total = 0
    for i in [1, 2, 3]:
        try:
            if i == 2:
                continue
            total = total + i
        finally:
            print("finally", i)
    return total


print(f())
