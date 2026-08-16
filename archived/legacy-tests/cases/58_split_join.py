# expect:
# hello
# world
# foo
# bar
# baz
# 3
# one,two,three
# a|b|c
# joined back: hello world

# Split into parts.
parts = "hello world".split(" ")
for p in parts:
    print(p)

ps = "foo,bar,baz".split(",")
for p in ps:
    print(p)
print(len(ps))

# Join from list literal.
joined = ",".join(["one", "two", "three"])
print(joined)
print("|".join(["a", "b", "c"]))

# Round-trip.
print("joined back:", " ".join("hello world".split(" ")))
