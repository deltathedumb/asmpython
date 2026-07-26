# expect:
# False
try:
    import nonexistent_module_xyz
    HAS = True
except ImportError:
    HAS = False
print(HAS)
# asmpython (beta/3.14.0) MISMATCH: prints 'True\n' (wrong).
