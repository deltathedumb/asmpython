# expect:
# hello, alice
# x=42 y=7
# 1 + 2 = 3
# pi is 3.14
# nested: <hello, bob>
name = "alice"
msg = f"hello, {name}"
print(msg)

x = 42
y = 7
print(f"x={x} y={y}")

print(f"{1} + {2} = {1+2}")

pi = 3.14
print(f"pi is {pi}")

inner = f"hello, {'bob'}"
print(f"nested: <{inner}>")
