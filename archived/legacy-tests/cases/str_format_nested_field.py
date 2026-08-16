# expect:
# b
print('{0[1]}'.format(['a', 'b', 'c']))
# asmpython (beta/3.14.0) rejects at compile: [E152] str.format() attribute/index access in fields (e.g. '{0.attr}', '{0[0]}') is not supported
