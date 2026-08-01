# tier: spec
# ref: reference/lexical_analysis.html#comments
# expect:
# 1
# # not a comment
# 2
x = 1  # a trailing comment

# another

print(x)
s = "# not a comment"
print(s)
print(len("a#b".split("#")))
