# probes: perf_counter advances across real work
# expect:
# 19999900000
# True
import time

start = time.perf_counter()
total = 0
for n in range(200000):
    total += n
end = time.perf_counter()
print(total)
print(end >= start)
