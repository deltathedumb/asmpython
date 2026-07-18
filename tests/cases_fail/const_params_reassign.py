# ext: const_params
# expect-error: locked by '@readonly' or the 'const_params' extension

def bump(x: int) -> int:
    x = x + 1
    return x

print(bump(3))
