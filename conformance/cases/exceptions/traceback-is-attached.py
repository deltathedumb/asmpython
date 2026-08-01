# tier: spec
# ref: library/exceptions.html
# expect:
# True
# tuple ('x',)
try:
    raise ValueError("x")
except ValueError as e:
    print(e.__traceback__ is not None)
    print(type(e.args).__name__, e.args)
