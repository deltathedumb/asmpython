# expect:
# x is 5
print('%(name)s is %(age)d' % {'name': 'x', 'age': 5})
# asmpython (beta/3.14.0) rejects at compile: [E133] bad format string: unsupported format character '('
