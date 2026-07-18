# ext: no_implicit_any
# expect-error: has no inferrable concrete type

def helper() -> int:
    return 1

f = helper
print(f())
