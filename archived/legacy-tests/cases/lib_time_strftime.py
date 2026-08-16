# expect:
# 2020
import time
t = time.struct_time((2020, 3, 15, 10, 30, 0, 6, 75, 0))
print(time.strftime('%Y', t))
# asmpython (beta/3.14.0) rejects at compile: [E120] module 'time' has no callable 'struct_time'
