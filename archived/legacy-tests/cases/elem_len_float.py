# probes: len() over the container (float elements)
# expect:
# 4
# True
xs = [1.5, 2.5, 3.5, 4.5]
print(len(xs))
print(len(xs) > 0)
