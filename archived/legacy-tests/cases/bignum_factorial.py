# expect:
# 15511210043330985984000000
def fact(n):
    r = 1
    for i in range(1, n + 1):
        r *= i
    return r
print(fact(25))
# asmpython (beta/3.14.0) MISMATCH: prints '7034535277573963776\n' (wrong).
