# ext: readonly_params
# expect:
# 8

@readonly(x)
def add_five(x: int) -> int:
    y = x + 5
    return y

print(add_five(3))
