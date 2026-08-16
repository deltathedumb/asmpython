# expect:
# 1
# 2
# 3
# 3
# 4
# 16
# 2
# AMY

# Exercises dict comprehensions: {key: value for var in iter [if cond]}.
# Covers int values, a filter clause, and str values via a method call.

words = ["a", "bb", "ccc"]
lengths = {w: len(w) for w in words}
print(lengths["a"])
print(lengths["bb"])
print(lengths["ccc"])
print(len(lengths))

nums = [1, 2, 3, 4]
squares = {str(n): n * n for n in nums if n % 2 == 0}
print(squares["2"])
print(squares["4"])
print(len(squares))

names = ["amy"]
upper = {n: n.upper() for n in names}
print(upper["amy"])
