# probes: %-format combines flags, width and precision
# expect:
# [+0003.14]
# [abc     ]
print("[%+08.2f]" % 3.14159)
print("[%-8.3s]" % "abcdef")
