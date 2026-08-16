# probes: randint's upper bound is inclusive
# expect:
# True
# True
import random

random.seed(2)
seen_low = False
seen_high = False
for _ in range(200):
    v = random.randint(1, 2)
    if v == 1:
        seen_low = True
    elif v == 2:
        seen_high = True
    else:
        print("out of range", v)
print(seen_low)
print(seen_high)
