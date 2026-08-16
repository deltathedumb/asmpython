# expect:
# a   b   c
# abc
# a       b

s: str = "a\tb\tc"
print(s.expandtabs(4))
print(s.expandtabs(0))
print("a\tb".expandtabs())
