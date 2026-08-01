# tier: spec
# ref: library/functions.html#print
# expect:
# a-b!
# through-a-name
# 1,2
# builtin_function_or_method
# nested-returns
# True
print("a", "b", sep="-", end="!\n")
f = print
f("through-a-name")
print(*[1, 2], sep=",")
print(type(print).__name__)
print(print("nested-returns") is None)
