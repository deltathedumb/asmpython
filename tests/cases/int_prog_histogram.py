# expect:
# 1:*
# 2:**
# 3:***
# 4:*
data = [1, 2, 2, 3, 3, 3, 4]
hist = {}
for x in data:
    hist[x] = hist.get(x, 0) + 1
bars = [str(k) + ':' + '*' * v for k, v in sorted(hist.items())]
for bar in bars:
    print(bar)
# asmpython (beta/3.14.0) MISMATCH: prints '8623552:*\n8623808:**\n8624288:***\n8625184:*\n' (wrong).
