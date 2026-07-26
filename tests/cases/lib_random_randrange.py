# expect:
# 19
import random
random.seed(5)
print(random.randrange(10, 20))
# asmpython (beta/3.14.0) rejects at compile: [E021] random.randrange() takes 1 argument(s), got 2
