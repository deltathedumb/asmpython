# expect:
# 42
# hi bob
# 7
# 50
double = lambda x: x * 2
print(double(21))
greet = lambda name: "hi " + name
print(greet("bob"))
add = lambda a, b: a + b
print(add(3, 4))
def apply(g, v):
    return g(v)
print(apply(lambda x: x * 10, 5))
