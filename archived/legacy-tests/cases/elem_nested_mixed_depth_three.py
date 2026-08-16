# probes: a three-level nesting of mixed kinds survives
# expect:
# (1, ['a', 2.5])
# a
# 2.5
# None
tree = {"rows": [(1, ["a", 2.5]), (2, ["b", None])]}
print(tree["rows"][0])
print(tree["rows"][0][1][0])
print(tree["rows"][0][1][1])
print(tree["rows"][1][1][1])
