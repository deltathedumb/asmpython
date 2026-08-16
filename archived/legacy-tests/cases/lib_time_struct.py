# expect:
# 1970
import time
t = time.gmtime(0)
print(t.tm_year)
# asmpython (beta/3.14.0) rejects at compile: [E120] module 'time' has no callable 'gmtime'
