import json
import yaml

lex = yaml.safe_load(open("data/lexicon.yaml", encoding="utf-8"))
for cat in ("drugs", "arms", "fake_docs", "mule_account"):
    em = lex["categories"][cat].get("emojis", [])
    print(cat, [repr(e) for e in em[:6]])
print("emoji_only_map:", list((lex.get("emoji_only_map") or {}).items())[:5])
posts = json.load(open("data/generated/posts.json", encoding="utf-8"))
with_emoji = [p for p in posts if any(ord(ch) > 0x1F000 for ch in p.get("text", ""))]
print("posts with real emoji:", len(with_emoji))
print("sample:", repr(posts[0].get("text"))[:200])
