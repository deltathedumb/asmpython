# tier: spec
# ref: reference/lexical_analysis.html#t-strings
# min-python: 3.14
# expect:
# Template
# ('hello ', '!')
# ['name']
# ['world']
# hello world
name = "world"
t = t"hello {name}!"
print(type(t).__name__)
print(t.strings)
print([i.expression for i in t.interpolations])
print([i.value for i in t.interpolations])
print("".join(t.strings[:1]) + str(t.interpolations[0].value))
