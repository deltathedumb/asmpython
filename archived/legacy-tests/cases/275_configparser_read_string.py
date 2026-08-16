# expect:
# 2
# example
# 8080
# true

import configparser

cfg = configparser.ConfigParser()
cfg.read_string("[server]\nhost = example\nport = 8080\n[flags]\nenabled = true\n")

secs: list = cfg.sections()
print(len(secs))
print(cfg.get("server", "host"))
print(cfg.get("server", "port"))
print(cfg.get("flags", "enabled"))
