# shopcart

A tiny shopping cart. Orders of **10 or more items** get a 10% bulk discount.

```python
from shopcart import Cart
cart = Cart()
cart.add("pen", 2.00, qty=10)
cart.total()
```
