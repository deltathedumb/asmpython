# expect:
# 42
# hello world
# True
# False
# None

from json import loads

# loads (numbers, strings, booleans, null)
print(loads("42"))
print(loads('"hello world"'))
print(loads("true"))
print(loads("false"))
print(loads("null"))
