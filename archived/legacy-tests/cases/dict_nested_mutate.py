# expect:
# {'x': {'y': 2}}
d = {'x': {'y': 1}}
d['x']['y'] = 2
print(d)
# nested dict value store yields a garbage pointer under asmpython (beta/3.14.0).
