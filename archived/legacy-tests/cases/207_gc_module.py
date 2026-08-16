# expect:
# 0
# 0

import gc

gc.enable()
gc.disable()
print(gc.isenabled())
print(gc.collect())
