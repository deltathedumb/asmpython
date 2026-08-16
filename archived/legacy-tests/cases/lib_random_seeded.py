# expect:
# 82
import random
random.seed(42)
print(random.randint(1, 100))
# asmpython (beta/3.14.0) MISMATCH: prints '76\n' (wrong).
