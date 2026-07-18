# ext: overload
# expect:
# int
# str

@overload
def describe(x: int) -> str:
    return "int"

@overload
def describe(x: str) -> str:
    return "str"

print(describe(5))
print(describe("hi"))
