# ext: const_params
# expect:
# 8
# 100

def add_five(x: int) -> int:
    y = x + 5
    return y

@mutable_params
def double(x: int) -> int:
    x = x * 2
    return x

print(add_five(3))
print(double(50))
