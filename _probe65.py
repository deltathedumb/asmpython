# probe65: isinstance, type checks, assert, global/nonlocal
x = 42
y = "hello"
z = [1, 2, 3]

print(isinstance(x, int))    # True
print(isinstance(y, str))    # True
print(isinstance(z, list))   # True
print(isinstance(x, str))    # False
print(isinstance(y, int))    # False

# assert
def check_positive(n: int) -> int:
    assert n > 0
    return n

print(check_positive(5))    # 5

# assert with message
def safe_div(a: int, b: int) -> int:
    assert b != 0, "division by zero"
    return a // b

print(safe_div(10, 2))   # 5

# nonlocal
def make_counter() -> int:
    count: int = 0
    def inc() -> int:
        nonlocal count
        count = count + 1
        return count
    inc()
    inc()
    return inc()

print(make_counter())   # 3

# global
g_val: int = 0

def set_global() -> None:
    global g_val
    g_val = 99

set_global()
print(g_val)   # 99
