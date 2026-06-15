# expect:
# 1
# 0
# 1

import sched

s: sched.scheduler = sched.scheduler()
print(s.empty())
e: sched.Event = s.enterabs(0.0, 1, 0, [], {})
print(s.empty())
s.cancel(e)
print(s.empty())
