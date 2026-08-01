# tier: spec
# ref: reference/lexical_analysis.html#string-and-bytes-literals
# expect:
# 'a\\b'
# b'ab'
# b'a\\b'
# 'ab'
# '2'
# 'multi\nline'
print(repr(r"a\b"))
print(repr(b"ab"))
print(repr(rb"a\b"))
print(repr(u"ab"))
print(repr(f"{1+1}"))
print(repr("""multi
line"""))
