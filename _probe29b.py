# isolate: conditional expression in list comp
xs = [x if x > 0 else -x for x in [-3, -1, 0, 2, 5]]
print(xs[0])  # 3
print(xs[3])  # 2
