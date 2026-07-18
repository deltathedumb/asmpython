# ext: assign_decorators
# expect:
# 1

calls = 0

def make_value() -> int:
    global calls
    calls = calls + 1
    return calls

def log_it(name: int, value: int) -> None:
    print(value)

@log_it
result = make_value()
