# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# True False
# False True
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)

def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)

print(is_even(10), is_odd(10))
print(is_even(7), is_odd(7))
