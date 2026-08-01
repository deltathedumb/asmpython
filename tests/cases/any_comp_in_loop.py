# expect:
# False
# True
# True
for c in range(3):
    print(any([i > 0 for i in range(c + 1)]))
