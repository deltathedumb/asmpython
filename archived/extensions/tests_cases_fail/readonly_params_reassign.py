# ext: readonly_params
# expect-error: locked by '@readonly'

@readonly(x)
def bump(x: int) -> int:
    x = x + 1
    return x

print(bump(3))
