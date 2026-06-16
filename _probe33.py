# probe33: recursive functions, fibonacci, ackermann
def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

print(fib(0))   # 0
print(fib(1))   # 1
print(fib(10))  # 55

# mutual recursion
def is_even(n: int) -> int:
    if n == 0:
        return 1
    return is_odd(n - 1)

def is_odd(n: int) -> int:
    if n == 0:
        return 0
    return is_even(n - 1)

print(is_even(4))   # 1
print(is_odd(5))    # 1
print(is_even(7))   # 0

# accumulator pattern
def fact(n: int) -> int:
    if n <= 0:
        return 1
    return n * fact(n - 1)

print(fact(5))   # 120
print(fact(10))  # 3628800
