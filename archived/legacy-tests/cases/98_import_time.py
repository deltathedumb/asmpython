# expect:
# 1
import time

# clock() returns CPU clock ticks >= 0
# (time.time() requires passing NULL to the C time() ptr arg; tested separately)
c = time.clock()
print(int(c >= 0))
