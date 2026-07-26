# expect:
# {1: 'a', 2: 'b'}
d = {'a': 1, 'b': 2}
print({v: k for k, v in d.items()})
# asmpython (beta/3.14.0) rejects at compile: [E054] dict comprehension keys must be strings (other key kinds work for lookup but not when the comprehension's result is iterated/printed)
