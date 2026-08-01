# tier: spec
# ref: library/numbers.html
# expect:
# True
# True
# True
# True
# False
import numbers

print(isinstance(1, numbers.Integral))
print(isinstance(1.5, numbers.Real))
print(isinstance(1, numbers.Number))
print(isinstance(True, numbers.Integral))
print(isinstance("x", numbers.Number))
