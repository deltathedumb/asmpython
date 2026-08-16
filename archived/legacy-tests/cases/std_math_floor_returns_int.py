# probes: math.floor/ceil return int, not float
# expect:
# int
# int
import math

print(type(math.floor(2.7)).__name__)
print(type(math.ceil(2.1)).__name__)
