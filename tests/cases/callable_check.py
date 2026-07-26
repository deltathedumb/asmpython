# expect:
# True False
def f():
    pass
print(callable(f), callable(5))
# asmpython (beta/3.14.0) MISMATCH: prints '1 0\n' (wrong).
