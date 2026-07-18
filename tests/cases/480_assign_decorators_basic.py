# ext: assign_decorators
# expect:
# 5
# 5

def decorator(name: int, value: int) -> None:
    print(name)
    print(value)

@decorator
my_var = 5
