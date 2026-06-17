# expect:
# [bob]
# [       bob]
# [3.14]
# [00101010]
# [1,234,567]
# ['bob']
# [   'bob']
# [bo]
# [b a]
# [***bob****]
name = "bob"
n = 42
pi = 3.14159

# str.format() supports the full [[fill]align]width.precision format-spec
# mini-language (same as f-strings), plus !r/!s/!a conversions -- via the
# same _gen_fstring_segment machinery.
print("[{}]".format(name))
print("[{:>10}]".format(name))
print("[{0:.2f}]".format(pi))
print("[{:08b}]".format(n))
print("[{:,}]".format(1234567))
print("[{!r}]".format(name))
print("[{0!r:>8}]".format(name))
print("[{:.2}]".format(name))
print("[{1} {0}]".format("a", "b"))
print("[{:*^10}]".format(name))
