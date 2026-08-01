# tier: spec
# ref: reference/lexical_analysis.html#encoding-declarations
# expect:
# 4
# 'caf\xe9'
# 1
# '\u4e2d\u6587'
# 2
s = "café"
print(len(s))
print(ascii(s))

caféname = 1
print(caféname)

print(ascii("中文"))
print(len("中文"))
