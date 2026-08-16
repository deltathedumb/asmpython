# expect:
# {'a': 1, 'b': 2, 'c': 3}
print(dict(zip('abc', [1, 2, 3])))
# asmpython (beta/3.14.0) rejects at compile: [E071] zip() arguments must be lists or tuples
