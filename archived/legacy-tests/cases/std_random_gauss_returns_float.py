# probes: random.gauss exists and returns a float
# expect:
# float
import random

random.seed(6)
print(type(random.gauss(0.0, 1.0)).__name__)
