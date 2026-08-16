# expect:
# dyn_foo
class D:
    def __getattr__(self, name):
        return 'dyn_' + name
print(D().foo)
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
