# expect:
# 0
# 1
# 1
# 2
# 3
# 5
# 8
# 13
# 21
# 34
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

for i in range(10):
    print(fib(i))
