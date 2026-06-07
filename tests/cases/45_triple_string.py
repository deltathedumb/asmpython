"""Module-level docstring.

Spans multiple lines and contains "embedded" and 'single' quotes;
should be parsed as a no-op ExprStmt and produce no output.
"""

# expect:
# hello
# world
# 11
# done
s = """hello
world"""
print(s[0:5])
print(s[6:11])
print(len(s))

# Single-quoted triple works too.
t = '''done'''
print(t)
