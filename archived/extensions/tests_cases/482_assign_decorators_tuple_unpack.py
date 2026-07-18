# ext: assign_decorators
# expect:
# 1
# 10
# 20

calls = 0

def make_pair() -> tuple[int, int]:
    global calls
    calls = calls + 1
    return (10, 20)

def log_pair(names, value: int) -> None:
    print(calls)

@log_pair
a, b = make_pair()
print(a)
print(b)
