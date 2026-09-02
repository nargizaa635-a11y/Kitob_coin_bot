# 📖 Kitob o'qib koin yig'ish — Telegram Mini App

Foydalanuvchilar kitob o'qib koin yig'adigan, do'stlarini taklif qilib bonus
oladigan va koinlarni sovg'alarga almashtiradigan to'liq ishlaydigan Telegram
Mini App (bot + Web App).

## ✅ Nima bor

- 🔒 Majburiy obuna (Force Join) — kanallarga a'zo bo'lmagan foydalanuvchi ilovani ocha olmaydi
- 👥 Referral tizimi — do'stini taklif qilgan foydalanuvchiga +150 koin
- 📚 Kitob o'qish — har sahifada 30 soniyalik taymer, tugagach +10 koin
- 🎁 Do'kon — koinlarga sovg'a/kitob sotib olish
- 🛠 Admin panel — tasodifiy g'olibni aniqlash (Randomizer), statistika
- 🔐 Xavfsiz — Telegram'ning rasmiy `initData` tekshiruvi orqali, hech kim
  boshqa birovning koinini o'zgartira olmaydi

## 📁 Fayllar

- `main.py` — bot (aiogram 3) + Mini App uchun API server (aiohttp), bittasi
- `index.html` — Mini App interfeysi (HTML+CSS+JS bitta faylda)
- `requirements.txt` — kerakli kutubxonalar
- `Procfile` — Railway uchun ishga tushirish buyrug'i

## 🚀 O'rnatish (Railway orqali)

Bu loyiha oldingi botlaringizdan farqli — u **ochiq URL manzilga** ega
bo'lishi kerak (chunki Mini App shu manzil orqali ochiladi). Shuning uchun
o'rnatish jarayoni bir oz boshqacharoq.

### 1. Bot yaratish

1. **@BotFather** ga o'ting, `/newbot` bilan bot yarating, tokenni saqlang

### 2. Majburiy obuna kanallarini tayyorlash

1. Telegram kanal(lar) yarating (yoki mavjudlaridan foydalaning)
2. **Botingizni shu kanal(lar)ga ADMIN qilib qo'shing** — bu shart, aks holda
   bot a'zolikni tekshira olmaydi
3. Kanal username'larini yozib qo'ying (masalan `@mening_kanalim`)

### 3. GitHub'ga yuklash

Barcha fayllarni (`main.py`, `index.html`, `requirements.txt`, `Procfile`)
GitHub repo'siga yuklang (avvalgi botlaringizda qilganingizdek).

### 4. `main.py` ni sozlash

GitHub'da `main.py` faylini tahrirlab, quyidagilarni to'ldiring:

```python
BOT_TOKEN = "BotFather bergan tokeningiz"
ADMIN_IDS = [sizning_telegram_id_raqamingiz]
FORCE_CHANNELS = ["@mening_kanalim"]  # o'z kanal(lar)ingiz
```

`WEBAPP_URL` ni hozircha shunday qoldiring — 6-qadamda to'ldiramiz:
```python
WEBAPP_URL = "https://SIZNING-DOMENINGIZ.up.railway.app"
```

### 5. Railway'da joylashtirish

1. railway.app da yangi loyiha yarating, GitHub repongizni ulang (oldingi
   botlardagi kabi)
2. **Muhim farq:** bu safar xizmatga **ochiq domen (Public Networking)**
   kerak bo'ladi:
   - Xizmat sozlamalarida (**Settings → Networking**) **"Generate Domain"**
     tugmasini bosing
   - Sizga `https://xxxxx.up.railway.app` ko'rinishidagi manzil beriladi —
     shuni nusxalab oling

### 6. `WEBAPP_URL` ni yangilash

1. Railway'dan olgan domenni GitHub'dagi `main.py` ichidagi `WEBAPP_URL`
   qatoriga qo'ying:
```python
WEBAPP_URL = "https://xxxxx.up.railway.app"
```
2. Commit qiling — Railway avtomatik qayta ishga tushiradi

### 7. Ma'lumotlar bazasi uchun Volume qo'shish (MUHIM!)

Kitoblar botida qilganimiz kabi, foydalanuvchilar va koinlar hech qachon
yo'qolmasligi uchun Volume qo'shishni unutmang:
1. Loyiha canvas'ida bo'sh joyga uzoq bosib, **"Create Volume"**
2. Mount path: `/data`
3. `main.py` dagi `DB_NAME = "/data/app.db"` allaqachon shu yo'lga mos
   yozilgan — qo'shimcha o'zgartirish kerak emas

### 8. BotFather'da Mini App tugmasini sozlash (ixtiyoriy, lekin tavsiya etiladi)

Bot xabarlaridagi "📚 Ilovani ochish" tugmasi allaqachon ishlaydi, lekin
bundan tashqari botning asosiy menyu tugmasiga ham qo'shish mumkin:
1. **@BotFather** → `/mybots` → botingiz → **Bot Settings** → **Menu Button**
2. **Configure menu button** → URL sifatida Railway domeningizni kiriting

## 🧭 Qanday ishlaydi

1. Foydalanuvchi botga `/start` bosadi (yoki referral havola orqali kiradi:
   `https://t.me/BOTUSERNAME?start=ref_123456789`)
2. Agar kanallarga a'zo bo'lmasa — obuna bo'lish so'raladi
3. A'zo bo'lgach — "📚 Ilovani ochish" tugmasi chiqadi
4. Mini App ochiladi: kitob o'qiydi (har sahifa 30 soniya + 10 koin),
   do'stlarini taklif qiladi (+150 koin har bir taklif uchun), do'kondan
   sovg'a sotib oladi
5. Admin `/admin` buyrug'i orqali tasodifiy g'olibni aniqlay oladi

## 🔐 Xavfsizlik haqida

Ilova Telegram'ning rasmiy `initData` tekshiruv formulasidan foydalanadi —
bu shuni anglatadiki, **hech kim** brauzer konsoli orqali so'rov yuborib,
boshqa birovning ID'sidan foydalanib, uning koinini o'g'irlab yoki
o'zgartirib bo'lmaydi. Har bir so'rov Telegram tomonidan raqamlangan
imzo bilan tasdiqlanadi.

## 🔧 Kengaytirish g'oyalari

- Bir nechta kitob qo'shish (hozir bitta namunaviy kitob bor)
- Kunlik bonus (har kuni botga kirganda +X koin)
- Do'kondagi xaridlarni admin panelda ko'rish va "yetkazib berish" belgisi
- Referral zanjiri (2-darajali takliflar uchun ham bonus)

