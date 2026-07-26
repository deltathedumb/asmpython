# expect:
# False 1
class Config:
    debug: bool = False
    level: int = 1
print(Config.debug, Config.level)
# asmpython (beta/3.14.0) MISMATCH: prints '0 1\n' (wrong).
