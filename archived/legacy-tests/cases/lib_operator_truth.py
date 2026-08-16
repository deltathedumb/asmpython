# expect:
# False True
import operator
print(operator.truth(0), operator.truth([1]))
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
