# ext: overload
# expect-error: no @overload signature for 'describe' accepts

@overload
def describe(x: int) -> str:
    return "int"

@overload
def describe(x: str) -> str:
    return "str"

print(describe(1, 2, 3))
