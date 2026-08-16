# expect:
# 8080 NA True
config = {'server': {'host': 'localhost', 'port': 8080}, 'debug': True, 'workers': 4}
def get(path, default=None):
    node = config
    for key in path.split('.'):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node
print(get('server.port'), get('server.missing', 'NA'), get('debug'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
