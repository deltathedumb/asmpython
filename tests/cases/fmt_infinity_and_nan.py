# probes: inf and nan have canonical spellings
# expect:
# inf
# -inf
# nan
# inf
inf = float("inf")
nan = float("nan")
print(inf)
print(-inf)
print(nan)
print(f"{inf}")
