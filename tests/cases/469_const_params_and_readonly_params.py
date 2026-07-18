# ext: const_params, readonly_params
# expect:
# 9

@readonly(x)
def add_one(x: int, y: int) -> int:
    z = y + 1
    return x + z

print(add_one(4, 4))
