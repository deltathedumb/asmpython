# tier: spec
# ref: reference/lexical_analysis.html#identifiers
# expect:
# 6
# True
# False False
# True
café = 1
Ω = 2
_private = 3
print(café + Ω + _private)
print("café".isidentifier())
print("1abc".isidentifier(), "a b".isidentifier())
print("_".isidentifier())
