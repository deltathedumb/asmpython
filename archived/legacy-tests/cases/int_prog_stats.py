# expect:
# 5.0 4.0
def stats(nums):
    n = len(nums)
    mean = sum(nums) / n
    variance = sum((x - mean) ** 2 for x in nums) / n
    return {'mean': mean, 'var': variance, 'min': min(nums), 'max': max(nums)}
r = stats([2, 4, 4, 4, 5, 5, 7, 9])
print(r['mean'], r['var'])
# asmpython (beta/3.14.0) rejects at compile: [E148] mixed dict value types (float and int); a float value can't share a dict with non-floats
