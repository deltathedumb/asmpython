# tier: spec
# ref: library/tomllib.html
# min-python: 3.11
# expect:
# ['a', 'b', 't']
# 1 x
# [1, 2]
# dict
import tomllib

data = tomllib.loads('a = 1\nb = "x"\n[t]\nc = [1, 2]\n')
print(sorted(data))
print(data["a"], data["b"])
print(data["t"]["c"])
print(type(data).__name__)
