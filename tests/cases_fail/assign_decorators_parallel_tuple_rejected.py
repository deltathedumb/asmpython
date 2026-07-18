# ext: assign_decorators
# expect-error: only supports a single-target or single-call tuple-unpack

def log_pair(names, value: int) -> None:
    print(value)

@log_pair
a, b = 1, 2
