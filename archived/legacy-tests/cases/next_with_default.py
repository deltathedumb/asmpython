# expect:
# 1 done
it = iter([1])
print(next(it), next(it, 'done'))
# asmpython (beta/3.14.0) MISMATCH: prints '1 0\n' (wrong).
