# tier: spec
# ref: peps.python.org/pep-0604/
# expect:
# int | str
# True
# True
# False
u = int | str
print(u)
print(isinstance(3, u))
print(isinstance('a', u))
print(isinstance(3.0, u))
