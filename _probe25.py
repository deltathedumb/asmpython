# probe25: list sorting with key, reversed, zip with 3 args
# sorted with key
words = ["banana", "apple", "cherry", "date"]
s = sorted(words)
print(s[0])   # apple
print(s[1])   # banana
print(s[3])   # date

# sorted with key=len (lambda)
s2 = sorted(words, key=lambda x: len(x))
print(s2[0])  # date (len=4)
print(s2[1])  # apple (len=5)

# reversed
xs = [1, 2, 3, 4, 5]
r = list(reversed(xs))
print(r[0])   # 5
print(r[4])   # 1

# zip with 3 iterables
a = [1, 2, 3]
b = [4, 5, 6]
c = [7, 8, 9]
for x, y, z in zip(a, b, c):
    print(x + y + z)
