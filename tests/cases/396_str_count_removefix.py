# expect:
# 2
# 0
# lo
# hello
# he
# hello

s = "hello"
print(s.count("l"))
print(s.count("x"))
print(s.removeprefix("hel"))
print(s.removeprefix("xyz"))
print(s.removesuffix("llo"))
print(s.removesuffix("xyz"))
