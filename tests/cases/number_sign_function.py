# expect:
# 1 -1 0
def sign(n):
    return (n > 0) - (n < 0)
print(sign(5), sign(-3), sign(0))
# asmpython (beta/3.14.0) MISMATCH: prints 'True True False\n' (wrong).
