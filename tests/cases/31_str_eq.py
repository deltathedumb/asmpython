# expect:
# 1
# 0
# 0
# 1
# match
# nope
print(int("foo" == "foo"))
print(int("foo" == "bar"))
print(int("foo" != "foo"))
print(int("foo" != "bar"))

a = "hello"
b = "hello"
if a == b:
    print("match")
else:
    print("nope")

c = "world"
if a == c:
    print("nope")
else:
    print("nope")
