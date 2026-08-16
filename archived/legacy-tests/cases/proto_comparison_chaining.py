# probes: a < b < c evaluates as a chain
# expect:
# evaluated 2
# True
# evaluated 2
# False
def note(v):
    print("evaluated " + str(v))
    return v


print(1 < note(2) < 3)
print(5 < note(2) < 3)
