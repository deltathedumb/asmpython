# expect:
# True
print('http://x'.startswith(('http://', 'https://')))
# asmpython (beta/3.14.0) rejects at compile: [E022] str.startswith() argument 1: expected str, got tuple
