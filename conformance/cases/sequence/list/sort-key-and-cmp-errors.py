# tier: spec
# ref: library/stdtypes.html#list.sort
# expect:
# [1, 2, 3]
# [3, 2, 1]
# ['a', 'bb', 'ccc']
# TypeError
xs = [3, 1, 2]
xs.sort()
print(xs)
xs.sort(reverse=True)
print(xs)
words = ["bb", "a", "ccc"]
words.sort(key=len)
print(words)
try:
    [1, "a"].sort()
except TypeError:
    print("TypeError")
