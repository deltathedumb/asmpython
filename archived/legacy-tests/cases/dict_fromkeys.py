# expect:
# {'a': 0, 'b': 0}
print(dict.fromkeys(['a', 'b'], 0))
# dict.fromkeys() is not modeled ([E113] type has no method 'fromkeys').
