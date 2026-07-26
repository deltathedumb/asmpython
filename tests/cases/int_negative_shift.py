# expect:
# -4 255
print(-8 >> 1, -1 & 0xFF)
# asmpython (beta/3.14.0) MISMATCH: prints '9223372036854775804 255\n' (wrong).
