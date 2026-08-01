# probes: True and False are singletons distinct from 1 and 0
# expect:
# True
# True
# True
# False
# True
print(True is True)
print(False is False)
print(1 == True)
print(1 is True)
print(0 == False)
