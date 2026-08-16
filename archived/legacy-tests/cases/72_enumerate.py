# expect:
# 0:a
# 1:b
# 2:c
# sum=36
items = ["a", "b", "c"]
for i, x in enumerate(items):
    print(str(i) + ":" + x)

nums = [10, 11, 12]
acc = 0
for idx, n in enumerate(nums):
    acc = acc + idx + n
print("sum=" + str(acc))   # (0+10)+(1+11)+(2+12) = 36
