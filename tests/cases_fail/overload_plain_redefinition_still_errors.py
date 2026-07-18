# ext: overload
# expect-error: redefined

def greet(x: int) -> int:
    return x

def greet(x: int) -> int:
    return x + 1

print(greet(1))
