# expect:
# 90.0
scores = [80, 90, 100]
avg = sum(scores) / len(scores)
print(round(avg, 2))
# asmpython (beta/3.14.0) MISMATCH: prints '0.0\n' (wrong).
