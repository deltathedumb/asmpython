# probe109: import patterns - math module
import math

print(math.floor(3.7))   # 3
print(math.ceil(3.2))    # 4
print(math.sqrt(16))     # 4.0 or 4?
print(math.gcd(48, 18))  # 6
print(math.factorial(5)) # 120
print(math.log2(8))      # 3.0 or 3?
print(math.pi)           # 3.14...
print(math.e)            # 2.71...
print(math.inf)          # inf

# math.isnan, math.isinf
print(math.isinf(math.inf))   # True
print(math.isinf(1.0))        # False
