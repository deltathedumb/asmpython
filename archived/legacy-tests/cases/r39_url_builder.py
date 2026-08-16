# expect:
# http://api.com?limit=10&page=1
def build_url(base, params):
    query = '&'.join(k + '=' + str(v) for k, v in sorted(params.items()))
    return base + '?' + query
print(build_url('http://api.com', {'page': 1, 'limit': 10}))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
