# expect:
# ['hello', 'world']
# hello
# True
# False
# HELLO WORLD
# hello world
# 3

s = "hello world"
parts = s.split(" ")
print(parts)
print(parts[0])
print(s.startswith("hello"))
print(s.startswith("world"))
print(s.upper())
print(s.lower())
print(s.count("l"))
