import json
from app.ml.llm import scanner

s = scanner()
print("available:", s.available, "model:", s.model)
print("ping:", s.ping())
cases = [
    "Fresh maal available 🍁🔌💨 2k per g, home delivery, DM",
    "Anyone selling desi katta 9mm? DM price",
    "Selling verified bank account kit with net banking OTP, 15k, commission daily",
    "New pharmacy open in Jaipur, medicines available",
    "PUBG new gun skin looks 🔫 amazing",
]
for text in cases:
    v = s.scan(text)
    print("\nTEXT:", text)
    print(json.dumps(v.as_dict(), ensure_ascii=False, indent=2)[:900])
