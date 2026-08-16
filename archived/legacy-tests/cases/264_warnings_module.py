# expect:
# Warning: test
# Warning: hello (file.py:5)
# ok

import warnings

warnings.warn("test")
warnings.warn_explicit("hello", 0, "file.py", 5)
warnings.filterwarnings("ignore")
warnings.simplefilter("ignore")
warnings.resetwarnings()
print("ok")
