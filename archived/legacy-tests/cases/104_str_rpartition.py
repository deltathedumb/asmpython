# expect:
# a/b . / . c
# ..abc
# ..

before, sep, after = "a/b/c".rpartition("/")
print(before + " . " + sep + " . " + after)

before, sep, after = "abc".rpartition("/")
print(before + "." + sep + "." + after)

before, sep, after = "".rpartition("x")
print(before + "." + sep + "." + after)
