# expect:
# b
scores = {'a': 10, 'b': 30, 'c': 20}
best = max(scores, key=lambda k: scores[k])
print(best)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
