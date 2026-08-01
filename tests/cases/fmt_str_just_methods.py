# probes: ljust/rjust/center pad to a width
# expect:
# [ab   ]
# [   ab]
# [--ab--]
print("[" + "ab".ljust(5) + "]")
print("[" + "ab".rjust(5) + "]")
print("[" + "ab".center(6, "-") + "]")
