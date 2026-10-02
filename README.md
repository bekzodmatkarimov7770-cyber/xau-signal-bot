# XAUUSD signal bot: SMC + Liquidity Sweep + Classic SNR

Bot faqat oltin (XAUUSD) uchun. U **o'zi xabar yubormaydi**: botga **signal** deb yozasiz,
u shu zahoti jonli narx bilan bozorni tahlil qiladi (yo'nalish, SNR zonalar, likvidlik, signal yoki nimani kutish kerakligi).
Signal faqat uchala sharoit birgalikda kelganda beriladi.

## Mantiq (SELL; BUY teskarisi)
1. **SNR**: H4 da narx kamida 2 marta qaytgan zona.
2. **Likvidlik**: teng yuqorilar (EQH), kecha/o'tgan hafta high'i, Osiyo sessiyasi high'i.
3. **Sweep**: M15 sham likvidlik ustiga soya chiqarib, qaytib uning ostida yopiladi; soya SNR zonasida.
4. **MSS/CHoCH**: sweepdan oldingi oxirgi swing-low pastga yopilish bilan buziladi (16 shamgacha).
5. **Kirish**: displacement qoldirgan FVG va/yoki Order Block (limit order, zona o'rtasi).
6. **SL**: sweep soyasi tashqarisida. **TP**: qarama-qarshi likvidlik yoki SNR zona, kamida 1:2.

Ball (12 dan): sweep 2 + SNR 2 + MSS 2 (majburiy) + FVG 1 + FVG/OB mos 1 + zona 3+ marta 1 +
yuqori sifatli likvidlik 1 + London/NY sessiyasi 1 + H4 EMA50 yo'nalishi 1. Standart chegara: 8.

## O'rnatish
1. **Ma'lumot**: twelvedata.com da ro'yxatdan o'ting, bepul API kalit oling (`TWELVE_API_KEY`).
   Kalit bo'lmasa, bot Yahoo `GC=F` (fyuchers) dan foydalanadi: narxi spotdan biroz farq qiladi.
2. **Bot**: @BotFather da bot tokeni oling. O'z Telegram ID'ingizni @userinfobot dan bilib oling.
3. **Vercel > Settings > Environment Variables**:
   - `BOT_TOKEN`, `ADMIN_ID`, `WEBHOOK_SECRET` (uzun tasodifiy so'z, faqat harf/raqam/_/-)
   - `TWELVE_API_KEY`
   - ixtiyoriy: `CRON_SECRET`, `MIN_SCORE`, `MIN_RR`, `ENTRY_TIMEOUT`
   - tavsiya: Storage > Upstash Redis ulang (takroriy signal himoyasi doimiy bo'ladi)
4. **Vercel > Settings > Deployment Protection**: "Vercel Authentication" ni **o'chiring**,
   aks holda Telegram va cron botga kira olmaydi.
5. Fayllarni GitHub repoga yuklang (Vercel o'zi joylashtiradi).
6. Brauzerda oching: `https://<sayt>.vercel.app/ulash?key=<WEBHOOK_SECRET>`
7. **(Ixtiyoriy) Avtomatik tekshiruv.** Standart rejim: faqat siz "signal" deganda tahlil qiladi, cron kerak emas.
   Agar bot o'zi ham signal yuborsin desangiz, cron-job.org (bepul) da vazifa yarating:
   - URL: `https://<sayt>.vercel.app/scan?key=<CRON_SECRET yoki WEBHOOK_SECRET>`
   - Jadval (UTC): `1,16,31,46 * * * *` (har 15 daqiqada, sham yopilgandan 1 daqiqa keyin)
   (Vercel Hobby rejasida cron faqat kuniga bir marta ishlaydi, shuning uchun tashqi cron kerak.)

## Telegram buyruqlari (faqat ADMIN_ID)
**`signal`** (yoki `/signal`) jonli tahlil | `/zones` SNR zonalar va likvidlik xaritasi |
`/backtest` strategiyani tarixda sinash | `/tarix` oxirgi signallar | `/sozlama`

## Ishga tushirishdan oldin
1. `/backtest` ni bosing va natijani o'qing (spred $0.30 hisobga olinadi).
2. 30-50 tadan kam savdo bo'lsa, natija ishonchli emas.
3. Kamida 2-4 hafta demo hisobda yoki juda kichik summada kuzating.

## Testlar
`python3 tests/test_smc.py` (mexanizm) va `python3 tests/test_bot.py` (server).
