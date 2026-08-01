# tier: spec
# ref: reference/lexical_analysis.html#string-and-bytes-literals
# expect:
# 3
# 4
# 'a\nb'
# 'a\\nb'
# quote:"q"
# 1
print(len("a\tb"))
print(len(r"a\tb"))
print(repr("a\nb"))
print(repr(r"a\nb"))
print("quote:\"q\"")
print(len("\\"))
