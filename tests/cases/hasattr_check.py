# expect:
# True False
class C:
    x = 1
c = C()
print(hasattr(c, 'x'), hasattr(c, 'y'))
# asmpython (beta/3.14.0) MISMATCH: prints '1 0\n' (wrong).
