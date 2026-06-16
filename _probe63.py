# probe63: string methods - find, rfind, index, split variations
s = "hello world hello"
print(s.find("hello"))      # 0
print(s.find("world"))      # 6
print(s.find("xyz"))        # -1
print(s.rfind("hello"))     # 12
print(s.rfind("xyz"))       # -1

# split with maxsplit
parts = "a,b,c,d".split(",", 2)
print(parts[0])  # a
print(parts[1])  # b
print(parts[2])  # c,d
print(len(parts))  # 3

# split on whitespace
words = "  hello   world  ".split()
print(len(words))  # 2
print(words[0])    # hello
print(words[1])    # world
