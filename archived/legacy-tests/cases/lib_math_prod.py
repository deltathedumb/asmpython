# expect:
# 24
import math
print(math.prod([1, 2, 3, 4]))
# asmpython (beta/3.14.0) rejects at compile: [E120] module 'math' has no callable 'prod'
