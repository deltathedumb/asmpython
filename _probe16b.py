# isolate closure issue
def outer(x: int) -> int:
    def inner(y: int) -> int:
        return x + y
    return inner(3)

print(outer(7))   # expected 10
print(outer(0))   # expected 3
print(outer(100)) # expected 103
