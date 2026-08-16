# expect:
# float
import random
random.seed(5)
print(type(random.gauss(0, 1)).__name__)
# asmpython (beta/3.14.0) rejects at compile: [E120] module 'random' has no callable 'gauss'
