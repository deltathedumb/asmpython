# probes: random.uniform stays within its bounds
# expect:
# True
import random

random.seed(5)
ok = True
for _ in range(50):
    v = random.uniform(1.0, 2.0)
    if v < 1.0 or v > 2.0:
        ok = False
print(ok)
