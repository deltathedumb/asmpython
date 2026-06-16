# probe79: sorted, reversed, list copy, enumerate on string
nums = [3, 1, 4, 1, 5, 9, 2, 6]

# sorted (returns new list)
s = sorted(nums)
print(s[0])       # 1
print(s[-1])      # 9
print(nums[0])    # 3 (unchanged)

# sorted with key
words = ["banana", "apple", "cherry", "date"]
sw = sorted(words, key=lambda w: len(w))
print(sw[0])   # date
print(sw[1])   # apple
print(sw[3])   # banana (or cherry)

# sorted reverse
sr = sorted(nums, reverse=True)
print(sr[0])   # 9
print(sr[-1])  # 1

# reversed
lst = [1, 2, 3, 4, 5]
rev = list(reversed(lst))
print(rev[0])   # 5
print(rev[4])   # 1

# enumerate on string
for i, c in enumerate("abc"):
    print(i, c)
# 0 a, 1 b, 2 c

# list copy
original = [1, 2, 3]
copy = original[:]
copy[0] = 99
print(original[0])   # 1
print(copy[0])       # 99
