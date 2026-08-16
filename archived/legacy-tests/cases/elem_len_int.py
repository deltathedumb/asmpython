# probes: len() over the container (int elements)
# expect:
# 4
# True
xs = [10, 20, 30, 40]
print(len(xs))
print(len(xs) > 0)
