# probes: a slice object exposes start/stop/step
# expect:
# 1
# 10
# 2
# [1, 3, 5, 7, 9]
s = slice(1, 10, 2)
print(s.start)
print(s.stop)
print(s.step)
print(list(range(20))[s])
