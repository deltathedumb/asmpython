# expect:
# 1
# 0
# 1

# `in` / `not in` against a value sema can't type precisely (a dict read out of
# an outer dict, here typed opaque) is lenient: it lowers to a dict-membership
# lookup. Exercises the int/any/instance RHS membership path.

outer = {"inner": {"a": 1, "b": 2}}
d = outer["inner"]          # typed opaque (nested dict value)
print("a" in d)             # 1
print("z" in d)             # 0
print("missing" not in d)   # 1
