# expect:
# 6
def f(a, b, c):
    return a + b + c


args = [1, 2, 3]
print(f(*args))
# f(*list) unpacking a list (not a literal tuple) is rejected ([E023]).
