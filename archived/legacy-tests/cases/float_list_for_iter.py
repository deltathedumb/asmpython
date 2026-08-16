# expect:
# 4.0
t = 0.0
for x in [1.5, 2.5]:
    t += x
print(t)
# asmpython (beta/3.14.0): "unsupported stmt For (float list elements)" --
# iterating a float list literal is rejected at compile time.
