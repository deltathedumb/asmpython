# expect:
# 2
# 3
# 6
# 11

# A function that RETURNS a bare function reference is callable at the call
# site: `get()(1)`. It used to be "[E113] 'any' is not callable", while sema's
# ARGUMENT path already typed the same bare name `callable:<ret>` -- which is
# what makes `apply(fn, v)` work. This is that rule on the return path.
#
# The closure lines are the guard, not decoration. A nested function is LIFTED
# to module level, so by name it is indistinguishable from an ordinary module
# function -- and typing a returned closure as a plain callable throws its
# binding away. Applying the rule to every module function fixed the first line
# here and broke `adder(1)(2)`, so it is restricted to functions with no free
# variables.
def add1(x):
    return x + 1


def get():
    return add1


def adder(x):
    def inner(y):
        return x + y
    return inner


def pick(flag):
    if flag:
        return add1
    return add1


print(get()(1))
print(adder(1)(2))
print(adder(1)(2) + 3)
print(pick(1)(10))
