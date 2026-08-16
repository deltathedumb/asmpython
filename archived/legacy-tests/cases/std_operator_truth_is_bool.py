# probes: operator.truth returns a real bool
# expect:
# False
# True
import operator

print(operator.truth([]))
print(operator.truth([0]))
