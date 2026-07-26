# expect:
# -5 3
import operator
print(operator.neg(5), operator.abs(-3))
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
