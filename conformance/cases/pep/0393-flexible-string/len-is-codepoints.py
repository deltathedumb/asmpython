# tier: spec
# ref: library/stdtypes.html#text-sequence-type-str
# expect:
# 3
# 233 128512
# 7
# True
s = "a\u00e9\U0001F600"
print(len(s))
print(ord(s[1]), ord(s[2]))
print(s.encode("utf-8").__len__())
print(s[2] == "\U0001F600")
