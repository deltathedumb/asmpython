# probes: bool prints as True/False, not 1/0
# expect:
# True
# False
# True
# True
# [True, False]
def passthrough(v):
    return v


print(True)
print(False)
print(passthrough(True))
b = 1 == 1
print(b)
print([True, False])
