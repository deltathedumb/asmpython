# expect:
# True
import math
print(math.isclose(0.1 + 0.2, 0.3))
# asmpython (beta/3.14.0) rejects at compile: [E021] math.isclose() takes 4 argument(s), got 2
