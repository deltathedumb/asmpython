# expect:
# 5.0
import math
print(math.dist([0, 0], [3, 4]))
# asmpython (beta/3.14.0) rejects at compile: [E120] module 'math' has no callable 'dist'
