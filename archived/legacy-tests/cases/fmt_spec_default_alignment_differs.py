# probes: numbers right-align, strings left-align
# expect:
# [x    ]
# [    3]
print("[" + format("x", "5") + "]")
print("[" + format(3, "5") + "]")
