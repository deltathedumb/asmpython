# tier: spec
# ref: peps.python.org/pep-0526/
# expect:
# 5
# False
# 1
# z
x: int = 5
print(x)
y: str
print('y' in dir())
class C:
    a: int = 1
    b: str = 'z'

print(C.a)
print(C.b)
