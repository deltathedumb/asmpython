# expect:
# 5
# hello
# world
# foo
# bar
# baz
# 1
# foo
# 1
tokens = []
tokens.append("hello")
tokens.append("world")
tokens.append("foo")
tokens.append("bar")
tokens.append("baz")
print(len(tokens))

for t in tokens:
    print(t)

print(int("foo" in tokens[2]))
print(tokens[2])
print(int(tokens[0] == "hello"))
