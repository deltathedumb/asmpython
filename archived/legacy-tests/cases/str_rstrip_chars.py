# expect:
# abc
print('abcxx'.rstrip('x'))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (str.rstrip)
