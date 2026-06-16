# probe: more patterns

# 1. str.format()
name = "world"
n = 42
# s = "hello {}".format(name)  # already works?
# s2 = "value={}".format(n)

# 2. multiple return values (tuple)
def divmod2(a: int, b: int) -> tuple:
    return (a // b, a % b)

q, r = divmod2(17, 5)
print(q)
print(r)

# 3. *args
def mysum(*args: int) -> int:
    total = 0
    for x in args:
        total += x
    return total

print(mysum(1, 2, 3, 4))

# 4. default parameter
def greet(name: str, greeting: str = "Hello") -> str:
    return greeting + " " + name

print(greet("Alice"))
print(greet("Bob", "Hi"))
