# expect:
# 20
def outer():
    x = 0
    def inner():
        nonlocal x
        x += 10
    inner()
    inner()
    return x
print(outer())
# asmpython (beta/3.14.0) rejects at compile: [E001] augmented assignment to undefined variable 'x'
