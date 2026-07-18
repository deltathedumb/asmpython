# ext: no_implicit_any
# expect:
# 3

def helper() -> int:
    return 3

f: int = helper()
print(f)
