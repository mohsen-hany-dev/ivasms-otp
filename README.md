# NumPlus Telegram Bot Client

بوت يقرأ رسائل OTP من API ويرسل الرسائل الجديدة إلى جروبات تيليجرام.

## المتطلبات
- Python 3.10+
- ملف `requirements.txt`

## التثبيت
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## ملفات الإعداد

### 1) `runtime_config.json`
هذا ملف القيم الأساسية الثابتة (توكن البوت، رابط API، تاريخ البداية الافتراضي...)

مثال توضيحي:
```json
{
  "API_BASE_URL": "https://your-api-domain.example.com",
  "API_START_DATE": "2025-01-01",
  "API_SESSION_TOKEN": "",
  "TELEGRAM_BOT_TOKEN": "123456789:EXAMPLE_BOT_TOKEN",
  "TELEGRAM_CHAT_ID": "-1001234567890",
  "BOT_LIMIT": "30"
}
```

### 2) `accounts.json`
حسابات تسجيل الدخول إلى API (يمكن إضافة أكثر من حساب):
```json
[
  {
    "name": "demo-account-1",
    "email": "demo1@example.com",
    "password": "DemoPassword#1",
    "enabled": true
  }
]
```

### 3) `groups.json`
الجروبات التي سيرسل لها البوت:
```json
[
  {
    "name": "demo-group",
    "chat_id": "-1001234567890",
    "enabled": true
  }
]
```

## CLI سهل (تفاعلي)
شغّل:
```bash
python cli.py
```
ستظهر قائمة اختيار مباشرة:
1. Add account
2. Add group
3. List accounts
4. List groups
5. Exit

مهم:
- عند اختيار `Add group` وحفظ `chat_id` بشكل صحيح، البوت سيقرأه من `groups.json` ويرسل له تلقائيًا.

## أوامر CLI المباشرة
```bash
python cli.py add-account --name acc1 --email you@example.com --password "YOUR_PASSWORD"
python cli.py add-group --name main --chat-id -1001234567890
python cli.py list-accounts
python cli.py list-groups
python cli.py clear-store
python cli.py clear-store --start-date 2025-01-01
python cli.py set-platform-emoji-id --key whatsapp --emoji-id 5472096095280572227
```

## التشغيل
تشغيل مستمر:
```bash
python bot.py
```

تشغيل دورة واحدة ثم خروج:
```bash
python bot.py --once
```

## ملاحظات التشغيل
- البوت يسأل عن `Start date` كل مرة تشغيل.
- باقي القيم الأساسية تُقرأ من `runtime_config.json` أو `.env`.
- إذا كان عندك حسابات صالحة في `accounts.json` لا تحتاج `API_SESSION_TOKEN` غالبًا.
