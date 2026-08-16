# expect:
# app 1.0
def parse_kv(text):
    result = {}
    for line in text.split('\n'):
        if '=' in line:
            k, v = line.split('=', 1)
            result[k.strip()] = v.strip()
    return result
cfg = parse_kv('name = app\nversion = 1.0')
print(cfg['name'], cfg['version'])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
