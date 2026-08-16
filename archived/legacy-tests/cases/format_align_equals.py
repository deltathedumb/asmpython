# expect:
# -     42 +     42
print(format(-42, '=8d'), format(42, '=+8d'))
# asmpython (beta/3.14.0) MISMATCH: prints '     -42      +42\n' (wrong).
