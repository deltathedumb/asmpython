# expect:
# [       bob]
# [bob       ]
# [   bob    ]
# [*******bob]
# [bob*******]
# [****bob****]
# [bob       ]
# [bob]
# [    42]
# [42    ]
# [  42  ]
# [00042]
# [00042]
# [******42]
# [    -7]
# [-0007]
# [      3.14]
# [3.14      ]
# [   3.14   ]
# [       1]
name = "bob"
n = 42
neg = -7
f = 3.14159
b = True

# Format-spec alignment/fill/width: [[fill]align][width] for str/int/float.
print(f"[{name:>10}]")
print(f"[{name:<10}]")
print(f"[{name:^10}]")
print(f"[{name:*>10}]")
print(f"[{name:*<10}]")
print(f"[{name:*^11}]")
print(f"[{name:10}]")  # bare width on str defaults to left-align
print(f"[{name}]")

print(f"[{n:>6}]")
print(f"[{n:<6}]")
print(f"[{n:^6}]")
print(f"[{n:05d}]")
print(f"[{n:0>5}]")
print(f"[{n:*>8}]")

print(f"[{neg:>6}]")
print(f"[{neg:05d}]")

print(f"[{f:>10.2f}]")
print(f"[{f:<10.2f}]")
print(f"[{f:^10.2f}]")

# A non-empty spec on a bool formats the underlying int (0/1), per
# CPython's bool.__format__ (inherited from int) -- not "True"/"False".
print(f"[{b:>8}]")
