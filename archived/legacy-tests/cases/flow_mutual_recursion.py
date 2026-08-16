# probes: two functions may recurse through each other
# expect:
# True
# True
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)


def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)


print(is_even(10))
print(is_odd(7))
