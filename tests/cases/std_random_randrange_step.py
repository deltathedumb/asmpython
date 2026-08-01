# probes: randrange(start, stop, step) honours the step
# expect:
# all even and in range
import random

random.seed(1)
for _ in range(10):
    v = random.randrange(0, 10, 2)
    if v % 2 != 0 or v < 0 or v >= 10:
        print("out of range", v)
print("all even and in range")
