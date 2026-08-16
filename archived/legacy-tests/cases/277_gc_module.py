# expect:
# 0
# 0
# 700
# 3

import gc

print(gc.isenabled())
gc.disable()
print(gc.isenabled())
gc.enable()
print(gc.get_threshold()[0])
gens: list = gc.get_count()
print(len(gens))
