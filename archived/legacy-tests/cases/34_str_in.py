# expect:
# 1
# 0
# 0
# 1
# match
# no
print(int("foo" in "foobar"))
print(int("baz" in "foobar"))
print(int("foo" not in "foobar"))
print(int("baz" not in "foobar"))

s = "hello world"
if "world" in s:
    print("match")
else:
    print("no")

if "xyz" in s:
    print("yes")
else:
    print("no")
