# expect:
# hello world
# foobar
# hello, alice!
# count = 5
a = "hello "
b = "world"
print(a + b)

print("foo" + "bar")

name = "alice"
greeting = "hello, " + name + "!"
print(greeting)

# Concat result usable in f-strings and stored to a local.
n = 5
msg = "count = " + str(n)
print(msg)
