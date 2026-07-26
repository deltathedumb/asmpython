# expect:
# 20.0
def avg(nums):
    return sum(nums) / len(nums)
print(avg([10, 20, 30]))
# asmpython (beta/3.14.0) MISMATCH: prints '3\n' (wrong).
