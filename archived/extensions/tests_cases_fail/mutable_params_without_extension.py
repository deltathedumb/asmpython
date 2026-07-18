# expect-error: requires the 'const_params' extension

@mutable_params
def bump(x: int) -> int:
    x = x + 1
    return x

print(bump(3))
