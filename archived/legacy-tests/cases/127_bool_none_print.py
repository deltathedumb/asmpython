# expect:
# True
# False
# None
# True
# False
# None
# True
# False
# False
# True
# True
# True
# False
# True
# True
# False
# None
# True
# False
# None
# True False None
# val=True
# <class 'bool'>
# <class 'bool'>
# <class 'NoneType'>

# print/str/repr/f-strings/type() must render bool and None values as
# "True"/"False"/"None" (matching CPython), not as the underlying 1/0/0 ints.

a = True
b = False
c = None
print(True)
print(False)
print(None)
print(a)
print(b)
print(c)
print(1 == 1)
print(1 != 1)
print(not True)
print(not False)
print(2 > 1 and 1 < 2)
print(True if 1 == 1 else False)
print(bool(0))
print(bool(5))
print(str(True))
print(str(False))
print(str(None))
print(repr(True))
print(repr(False))
print(repr(None))
print(f"{True} {False} {None}")
print(f"val={a}")
print(type(True))
print(type(False))
print(type(None))
