# probes: tracemalloc reports whether it is tracing
# expect:
# False
# True
# False
import tracemalloc

print(tracemalloc.is_tracing())
tracemalloc.start()
print(tracemalloc.is_tracing())
tracemalloc.stop()
print(tracemalloc.is_tracing())
