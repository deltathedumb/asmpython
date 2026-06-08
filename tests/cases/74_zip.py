# expect:
# 1+10=11
# 2+20=22
# 3+30=33
# a=x
# b=y
# c=z
# 0: 5 / one
# 1: 6 / two
# 2: 7 / three
# short: 1-100
# short: 2-200
# total=66
nums = [1, 2, 3]
tens = [10, 20, 30]
for n, t in zip(nums, tens):
    print(str(n) + "+" + str(t) + "=" + str(n + t))

letters = ["a", "b", "c"]
vals = ["x", "y", "z"]
for k, v in zip(letters, vals):
    print(k + "=" + v)

ids = [5, 6, 7]
names = ["one", "two", "three"]
for i, (num, name) in enumerate(zip(ids, names)):
    print(str(i) + ": " + str(num) + " / " + name)

# zip stops at the shorter sequence.
a = [1, 2]
b = [100, 200, 300]
for x, y in zip(a, b):
    print("short: " + str(x) + "-" + str(y))

# zip result used in a running computation.
total = 0
for p, q in zip(nums, tens):
    total = total + p + q
print("total=" + str(total))
