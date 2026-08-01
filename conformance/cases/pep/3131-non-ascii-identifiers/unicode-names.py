# tier: spec
# ref: reference/lexical_analysis.html#identifiers
# expect:
# 6
# True
# True
# False
# True
λ = 1
café = 2
_中 = 3
print(λ + café + _中)
print("λ".isidentifier())
print("é".isidentifier())
print("1λ".isidentifier())
print("_中".isidentifier())
