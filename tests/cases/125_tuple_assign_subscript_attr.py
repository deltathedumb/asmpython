# expect:
# [2, 1, 3, 4]
# [4, 1, 3, 2]
# [4, 1, 100, 2]
# 3
# 2
# 1
# 2
# [4, 1, 100, 2]
# ['c', 'b', 'a']


nums = [1, 2, 3, 4]
nums[0], nums[1] = nums[1], nums[0]
print(nums)

i = 0
j = 3
nums[i], nums[j] = nums[j], nums[i]
print(nums)

a = 100
nums[2], a = a, nums[2]
print(nums)
print(a)


class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


p = Point(1, 2)
p.x, p.y = p.y, p.x
print(p.x)
print(p.y)

p.x, nums[3] = nums[3], p.x
print(p.x)
print(nums)

words = ["a", "b", "c"]
words[0], words[2] = words[2], words[0]
print(words)
