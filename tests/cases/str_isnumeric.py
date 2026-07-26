# expect:
# True False
print('123'.isnumeric(), '12.3'.isnumeric())
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (str.isnumeric)
