# probe: various patterns

# 1. str.split with maxsplit
s = "a,b,c,d"
parts = s.split(",")
print(len(parts))
print(parts[0])
print(parts[3])

# 2. str.strip / lstrip / rstrip
s2 = "  hello  "
print(s2.strip())
print(s2.lstrip())
print(s2.rstrip())

# 3. str.replace
s3 = "hello world"
print(s3.replace("world", "there"))

# 4. isinstance with multiple types
def check(x: int) -> str:
    if isinstance(x, int):
        return "int"
    return "other"

print(check(42))

# 5. global keyword
counter: int = 0
def increment() -> None:
    global counter
    counter += 1

increment()
increment()
print(counter)
