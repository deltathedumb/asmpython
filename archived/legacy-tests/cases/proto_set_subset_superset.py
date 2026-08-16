# probes: set comparison operators test containment
# expect:
# True
# True
# False
print({1, 2} <= {1, 2, 3})
print({1, 2, 3} >= {1, 2})
print({1, 4} <= {1, 2, 3})
