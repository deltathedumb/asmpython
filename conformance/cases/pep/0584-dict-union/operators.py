# tier: spec
# ref: peps.python.org/pep-0584/
# expect:
# {'x': 1, 'y': 9, 'z': 3}
# {'y': 2, 'z': 3, 'x': 1}
# {'x': 1, 'y': 9, 'z': 3}
# {'x': 1, 'y': 2}
a = {'x': 1, 'y': 2}
b = {'y': 9, 'z': 3}
print(a | b)
print(b | a)
c = dict(a)
c |= b
print(c)
print(a)
