# expect:
# 6.6
total = 0.0
for x in [1.1, 2.2, 3.3]:
    total = total + x
print(round(total, 1))
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt For (float list elements)
