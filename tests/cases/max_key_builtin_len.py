# expect:
# bbb
print(max(['a', 'bbb', 'cc'], key=len))
# key= must be a lambda literal ([E135]); a builtin like len is rejected.
