# expect:
# 10 True
class C:
    pass
c = C()
setattr(c, 'x', 10)
print(getattr(c, 'x'), hasattr(c, 'x'))
# asmpython (beta/3.14.0) MISMATCH: prints '10 1\n' (wrong).
