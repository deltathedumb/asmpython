# probe36: tuple operations, namedtuple-like, dataclass-like
t = (1, "hello", 3.14)
print(t[0])    # 1
print(t[1])    # hello
print(len(t))  # 3

# tuple unpacking in various contexts
a, b, c = t
print(a)   # 1
print(b)   # hello

# nested tuple
nested = ((1, 2), (3, 4))
x, y = nested
print(x[0])   # 1
print(y[1])   # 4

# tuple in a list
pairs = [(1, "a"), (2, "b"), (3, "c")]
for n, s in pairs:
    print(n, s)

# return multiple values
def swap(a: int, b: int) -> tuple:
    return b, a

x2, y2 = swap(1, 2)
print(x2, y2)  # 2 1
