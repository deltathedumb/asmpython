# expect:
# 3
# True

import random

result = random.sample([10, 20, 30, 40, 50], 3)
print(len(result))
bits = random.getrandbits(8)
print(bits >= 0)
