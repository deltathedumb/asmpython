# probe35: list slicing, string slicing
xs = [0, 1, 2, 3, 4, 5]
print(xs[1:4])    # [1, 2, 3]
print(xs[::2])    # [0, 2, 4]
print(xs[:3])     # [0, 1, 2]
print(xs[3:])     # [3, 4, 5]
print(xs[-2:])    # [4, 5]
print(xs[1:5:2])  # [1, 3]

# string slicing
s = "hello world"
print(s[0:5])   # hello
print(s[6:])    # world
print(s[:5])    # hello
print(s[::2])   # hlowrd
print(s[-5:])   # world

# slice with negative step
xs2 = [1, 2, 3, 4, 5]
print(xs2[::-1])   # [5, 4, 3, 2, 1]
