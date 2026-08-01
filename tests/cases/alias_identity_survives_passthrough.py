# probes: a returned argument is the same object
# expect:
# True
def passthrough(v):
    return v


a = [1]
print(passthrough(a) is a)
