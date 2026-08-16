# expect:
# True
# False
isk = frozenset(["def", "for"]).__contains__
print(isk("def"))
print(isk("x"))
# Reduced from the stdlib `keyword` library, whose iskeyword is literally
# `iskeyword = frozenset(kwlist).__contains__`. asmpython (beta/3.14.0) prints
# NOTHING: a bound method (frozenset(...).__contains__) assigned to a module
# global and then called is not modeled. Direct `x in frozenset(...)` works.
