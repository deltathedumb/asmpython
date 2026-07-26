# expect:
# 4.0
print(sum([1.5, 2.5]))
# asmpython (beta/3.14.0) prints a garbage int bit-pattern
# (9222246136947933184): sum() seeds the accumulator with int 0 and the
# float add lands the IEEE bits in a GP register instead of xmm.
