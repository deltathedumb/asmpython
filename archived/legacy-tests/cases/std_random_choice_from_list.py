# probes: random.choice returns a member of its input
# expect:
# True
import random

random.seed(7)
options = ["a", "b", "c"]
ok = True
for _ in range(30):
    if random.choice(options) not in options:
        ok = False
print(ok)
