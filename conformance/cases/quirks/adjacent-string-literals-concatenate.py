# tier: spec
# ref: reference/lexical_analysis.html#string-literal-concatenation
# expect:
# abcd
# 4
# ['ab', 'c']
s = "ab" "cd"
print(s)
print(len(s))
parts = ["a" "b", "c"]
print(parts)
