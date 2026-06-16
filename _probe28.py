# probe28: string methods, list methods
# str.title, str.swapcase, str.zfill
print("hello world".title())         # Hello World
print("Hello World".swapcase())      # hELLO wORLD
print("42".zfill(5))                 # 00042
print("-42".zfill(5))                # -0042

# str.partition
s = "hello:world:foo"
a, sep, b = s.partition(":")
print(a)    # hello
print(sep)  # :
print(b)    # world:foo

# list.extend
xs = [1, 2, 3]
xs.extend([4, 5])
print(len(xs))   # 5
print(xs[4])     # 5

# list.index
print(xs.index(3))   # 2
print(xs.index(1))   # 0

# list.reverse (in-place)
ys = [1, 2, 3]
ys.reverse()
print(ys[0])   # 3
print(ys[2])   # 1
