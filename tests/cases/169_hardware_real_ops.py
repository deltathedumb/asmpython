# expect:
# True
# True
# True
# True
# 0
# 0

# asmlib.hardware has no CPython equivalent; this checks that rdtsc/cpuid/
# rdrand are real (non-stub) on the hosted target, and that ring-0-only ops
# (in_byte, halt, ...) remain safe zero-returning no-ops rather than faulting.
from asmlib.hardware import rdtsc, cpuid, rdrand, in_byte, halt

t1 = rdtsc()
t2 = rdtsc()
print(t1 > 0)
print(t2 >= t1)

eax = cpuid(0)
print(eax > 0)

r1 = rdrand()
r2 = rdrand()
print(r1 != 0 or r2 != 0)

print(in_byte(0x80))
print(halt())
