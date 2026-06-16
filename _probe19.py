# probe19: set comprehension, multiple assignment
ns = [1, 2, 2, 3, 3, 3]
s = {str(x) for x in ns}
print(len(s))   # 3

# multiple assignment
a = b = 0
a += 1
b += 2
print(a)   # 1
print(b)   # 2
