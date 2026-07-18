# ext: no_shadowing
# expect-error: shadows a module-level global

total = 0

def f() -> int:
    total = 5
    return total

print(f())
