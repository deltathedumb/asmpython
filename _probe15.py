# probe15: list.remove, list.count, str.count, str.center/ljust/rjust
xs = [1, 2, 3, 2, 4]
xs.remove(2)
print(xs[0], xs[1], xs[2], xs[3])  # 1 3 2 4

print(xs.count(2))   # 1
print(xs.count(1))   # 1

# str.count
s = "hello world"
print(s.count("l"))  # 3
print(s.count("o"))  # 2

# str.center/ljust/rjust
print("hi".center(6))     # "  hi  "
print("hi".ljust(5))      # "hi   "
print("hi".rjust(5))      # "   hi"
print("hi".center(6, "*"))  # "**hi**"
