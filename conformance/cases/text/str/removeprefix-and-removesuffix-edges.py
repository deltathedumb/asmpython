# tier: spec
# ref: library/stdtypes.html#str.removeprefix
# expect:
# def
# abcdef
# abc
# abcdef
# aa
print("abcdef".removeprefix("abc"))
print("abcdef".removeprefix("xyz"))
print("abcdef".removesuffix("def"))
print("abcdef".removesuffix(""))
print("aaa".removeprefix("a"))
