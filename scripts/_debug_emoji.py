import emoji
from app.ml.emoji_signals import _catalogue, _key, scan

t = "fresh stock \U0001F341\U0001F50C\U0001F4A8 2k per g"
print("list:", emoji.emoji_list(t))
c = _catalogue()
print("catalogue sample:", [(repr(k), v["category"]) for k, v in list(c.items())[:3]])
print("contains leaf:", repr(_key("\U0001F341")), _key("\U0001F341") in c)
print("scan:", [(s.emoji, s.category) for s in scan(t)])
