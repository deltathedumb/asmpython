# expect:
# ['A', 'B']
print(list(map(str.upper, ['a', 'b'])))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (map() with a non-lambda predicate)
