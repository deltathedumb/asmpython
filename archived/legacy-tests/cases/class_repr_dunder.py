# expect:
# REPR
# [REPR]
class P:
    def __repr__(self):
        return 'REPR'
print(repr(P()))
print([P()])
# asmpython (beta/3.14.0) MISMATCH: prints 'REPR\n[8689104]\n' (wrong).
