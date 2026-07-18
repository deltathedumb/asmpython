# ext: overload
# expect-error: are indistinguishable

@overload
def describe(x: int) -> str:
    return "a"

@overload
def describe(x: int) -> str:
    return "b"

print(describe(1))
