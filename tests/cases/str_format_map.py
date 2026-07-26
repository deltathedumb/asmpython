# expect:
# 12
print('{a}{b}'.format_map({'a': 1, 'b': 2}))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (str.format_map)
