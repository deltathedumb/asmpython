# probes: a large int formats without loss
# expect:
# 1267650600228229401496703205376
# 1267650600228229401496703205376
# 1,267,650,600,228,229,401,496,703,205,376
n = 2 ** 100
print(n)
print(f"{n}")
print(format(n, ","))
