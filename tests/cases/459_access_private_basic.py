# ext: access
# expect:
# 42

class Account:
    def __init__(self, balance: int) -> None:
        self.balance = balance

    @private
    def _raw_balance(self) -> int:
        return self.balance

    def get_balance(self) -> int:
        return self._raw_balance()

a = Account(42)
print(a.get_balance())
