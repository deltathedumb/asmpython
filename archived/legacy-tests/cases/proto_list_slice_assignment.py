# probes: assigning to a slice splices the list
# expect:
# [1, 'a', 4]
xs = [1, 2, 3, 4]
xs[1:3] = ["a"]
print(xs)
