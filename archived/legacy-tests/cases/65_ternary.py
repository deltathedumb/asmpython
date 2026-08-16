# expect:
# big
# 7
# 3
# small
# 2.5
# nested-b
x = 10
print("big" if x > 5 else "small")

a = 7
b = 3
print(a if a > b else b)
print(b if a > b else a)

y = 2
print("big" if y > 5 else "small")

# int/float promotion: one arm int, the other float -> float result
f = 2.5 if x > 5 else 1
print(f)

# nested / right-associative ternary
p = 0
q = 1
print("nested-a" if p else "nested-b" if q else "nested-c")
