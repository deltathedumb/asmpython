# ext: readonly_params
# expect-error: is not a parameter of

@readonly(z)
def bump(x: int) -> int:
    return x + 1

print(bump(3))
