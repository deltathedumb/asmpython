# tier: spec
# ref: reference/lexical_analysis.html#keywords
# expect:
# 1 2 3
# 4
# C
match = 1
case = 2
type = 3
print(match, case, type)
_ = 4
print(_)
class C:
    pass
print(C.__name__)
