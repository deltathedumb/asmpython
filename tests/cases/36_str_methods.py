# expect:
# HELLO WORLD
# hello world
# foo bar
# foo bar
# foo bar
# 1
# 0
# 1
# 0
# 6
# -1
# 3
# 0
# Hi there, Hi everyone
# the cat sat
print("Hello World".upper())
print("Hello World".lower())

s = "   foo bar   "
print(s.strip())
print(s.lstrip().rstrip())
print(s.rstrip().lstrip())

print(int("hello".startswith("he")))
print(int("hello".startswith("zz")))
print(int("hello".endswith("lo")))
print(int("hello".endswith("zz")))

print("hello world".find("world"))
print("hello world".find("xyz"))
print("ababab".count("ab"))
print("aaa".count("z"))

print("Hello there, Hello everyone".replace("Hello", "Hi"))
print("the dog sat".replace("dog", "cat"))
