# probes: gc.collect returns an integer
# expect:
# int
# True
import gc

collected = gc.collect()
print(type(collected).__name__)
print(collected >= 0)
