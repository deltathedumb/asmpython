# expect:
# b
import random
random.seed(1)
print(random.choice(['a', 'b', 'c', 'd']))
# asmpython (beta/3.14.0) MISMATCH: prints '5368741890\n' (wrong).
