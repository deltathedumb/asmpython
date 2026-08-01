# tier: spec
# ref: reference/datamodel.html#object.__repr__
# expect:
# ['a', 'b']
# ['a', 'b']
# a
# a
# {'k': 'v'}
# (1, 'a')
xs = ["a", "b"]
print(xs)
print(str(xs))
print("a")
print(str("a"))
print({"k": "v"})
print((1, "a"))
