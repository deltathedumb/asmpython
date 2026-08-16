# expect:
# val
import configparser
c = configparser.ConfigParser()
c.read_string('[s]\nkey = val')
print(c['s']['key'])
# asmpython (beta/3.14.0) rejects at compile: [E017] 'ConfigParser' object does not support indexing
