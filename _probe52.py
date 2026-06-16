# probe52: string operations
# split with delimiter
s = "a,b,c,d"
parts = s.split(",")
print(parts)          # ['a', 'b', 'c', 'd']
print(len(parts))     # 4

# join
joined = "-".join(parts)
print(joined)         # a-b-c-d

# strip variants
s2 = "  hello  "
print(s2.strip())     # hello
print(s2.lstrip())    # hello
print(s2.rstrip())    #   hello  (trailing trimmed? no, rstrip removes trailing)

# String slicing
s3 = "hello world"
print(s3[0:5])    # hello
print(s3[6:])     # world
print(s3[:5])     # hello
print(s3[-5:])    # world
print(s3[::2])    # hlowrd (every other char)

# String contains
print("ell" in "hello")   # True
print("xyz" in "hello")   # False

# String * int
print("ha" * 3)   # hahaha

# upper/lower
print("Hello World".upper())   # HELLO WORLD
print("Hello World".lower())   # hello world
