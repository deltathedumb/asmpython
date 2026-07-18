# ext: no_shadowing
# expect-error: shadows a module-level global

count = 0

def f(count: int) -> int:
    return count + 1

print(f(5))
