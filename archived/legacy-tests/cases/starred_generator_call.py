# expect:
# 10
def total(*nums):
    return sum(nums)
print(total(*range(5)))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types
