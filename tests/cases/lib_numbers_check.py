# expect:
# True True
import numbers
print(isinstance(5, numbers.Number), isinstance(5.0, numbers.Real))
# asmpython (beta/3.14.0) rejects at compile: asmpython: 'XmmLoc' object has no attribute 'offset'
