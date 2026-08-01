# probes: < left-aligns within a width
# expect:
# [x    ]
# [42   ]
print("[" + format("x", "<5") + "]")
print("[" + format(42, "<5") + "]")
