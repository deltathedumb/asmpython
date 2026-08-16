# expect:
# [hello]
# [hi]
# [     hello]
# [hello     ]
# [    hello     ]
# [*******hello]
# []
# [hello]
name = "hello world"
short = "hi"

# `.precision` on a str segment truncates to the first `precision` chars
# (a no-op if the string is already shorter), combinable with
# alignment/width and the `s` type char.
print(f"[{name:.5}]")
print(f"[{short:.5}]")
print(f"[{name:>10.5}]")
print(f"[{name:10.5}]")
print(f"[{name:^14.5}]")
print(f"[{name:*>12.5}]")
print(f"[{name:.0}]")
print(f"[{name:.5s}]")
