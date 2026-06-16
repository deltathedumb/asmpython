# probe41: string methods
s = "  hello world  "
print(s.strip())        # hello world
print(s.lstrip())       # hello world
print(s.rstrip())       #   hello world

t = "hello"
print(t.center(11))     #    hello
print(t.ljust(10))      # hello
print(t.rjust(10))      #      hello

u = "abcabc"
print(u.count("b"))     # 2
print(u.count("bc"))    # 2

print("abc".startswith("ab"))  # 1
print("abc".endswith("bc"))    # 1
print("abc".startswith("bc"))  # 0

print("hello".replace("l", "r"))  # herro

v = "42"
print(v.zfill(5))       # 00042
print(v.zfill(1))       # 42
