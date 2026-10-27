# Bulk discount is not applied at exactly 10 items

The README says orders of 10 or more items get a 10% bulk discount.

Steps to reproduce:

```python
from shopcart import Cart
cart = Cart()
cart.add("pen", 2.00, qty=10)
print(cart.total())
```

Expected: `18.0` (20.00 minus 10%).
Actual: `20.0`. With 11 items the discount is applied, so it looks like an off-by-one.
