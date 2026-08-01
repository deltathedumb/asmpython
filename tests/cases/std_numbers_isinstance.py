# probes: int/float register as numbers.Number
# expect:
# True
# True
# False
import numbers

print(isinstance(1, numbers.Number))
print(isinstance(1.5, numbers.Number))
print(isinstance("x", numbers.Number))
