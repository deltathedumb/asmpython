def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)

print("fib(10) =")
print(fib(10))

i = 0
while i < 5:
    print(i * i)
    i = i + 1
