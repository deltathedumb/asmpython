# expect:
# True
print('file.py'.endswith(('.py', '.txt')))
# asmpython (beta/3.14.0) rejects at compile: [E022] str.endswith() argument 1: expected str, got tuple
