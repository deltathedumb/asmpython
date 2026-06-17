# expect:
# 6
# -1
# 6
# 7
# 16
# caught

s = "hello world"
print(s.find("world"))
print(s.find("xyz"))
print(s.index("world"))
print(s.rindex("o"))

print(sum([1, 2, 3], 10))

try:
    s.index("xyz")
except ValueError:
    print("caught")
