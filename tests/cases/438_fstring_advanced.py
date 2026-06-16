# expect:
# width=10, value=3.14
# name=Alice
# hello WORLD

x: float = 3.14
w: int = 10
print(f"width={w}, value={x}")

name: str = "Alice"
print(f"name={name}")

s1: str = "hello"
s2: str = "world"
print(f"{s1} {s2.upper()}")
