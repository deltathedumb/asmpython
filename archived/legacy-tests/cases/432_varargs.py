# expect:
# 6
# 0
# 10
# hello world foo

def my_sum(*args: int) -> int:
    total: int = 0
    for x in args:
        total += x
    return total

print(my_sum(1, 2, 3))
print(my_sum())
print(my_sum(1, 2, 3, 4))

def join(*args: str) -> str:
    result: str = ""
    first: bool = True
    for s in args:
        if not first:
            result += " "
        result += s
        first = False
    return result

print(join("hello", "world", "foo"))
