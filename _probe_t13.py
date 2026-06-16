# context managers (with statement)
class ManagedResource:
    def __init__(self, name: str) -> None:
        self.name = name
    
    def __enter__(self):
        print("entering", self.name)
        return self
    
    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> bool:
        print("exiting", self.name)
        return False

with ManagedResource("res") as r:
    print("inside", r.name)

print("after with")
