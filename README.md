# XAUUSD signal bot: SMC + Liquidity Sweep + Classic SNR, alohida ICT va Auto Fibo modellari

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

## ICT modeli (alohida, `ict` buyrug'i)
SMC signalidan mustaqil ishlaydi. Vaqtlar Nyu-York vaqti bilan (yozgi/qishki vaqt avtomatik).
1. **Killzone**: sweep London (02:00-05:00 NY) yoki NY AM (07:00-11:00 NY, Silver Bullet 10-11 ichida) da bo'lishi shart.
2. **Likvidlik**: Osiyo diapazoni (20:00-00:00 NY) high/low, kechagi kun high/low (PDH/PDL), NY da London high/low.
3. **Sweep (Judas swing)** daraja ustidan/ostidan, keyin **MSS** displacement bilan (3 soat ichida).
4. **FVG** displacement oyog'ida qolishi shart. Kirish: FVG o'rtasi (CE), limit order.
5. **SL** sweep cho'qqisi ortida, **TP** qarama-qarshi likvidlik, kamida 1:2.

Ball (10 dan): asos 5 + kuchli displacement 1 + OTE (0.62-0.79) bilan ustma-ust 1 + H4 struktura mos 1 +
Midnight Open'dan premium/discount tomonda 1 + tashqi likvidlik (Osiyo/PDH/PDL) 1. Standart chegara: 7 (`ICT_MIN_SCORE`).
ICT signali faqat siz `ict` deb yozganda chiqadi, avtomatik skanerga qo'shilmagan.

## Auto Fibo + SNR (alohida, `fibo` buyrug'i)
Bot M15, M5 va M1 da trendni o'zi aniqlaydi va Fibo'ni o'zi tortadi. Qoidalar qat'iy:
bir xil shamlar berilsa, Fibo har doim aynan bir xil nuqtalarga tortiladi.
1. **Impuls**: strukturani buzgan (BOS) oyoq. A = impuls boshlangan swing (buzilmagan), B = eng chekka narx.
2. **Trend**: oxirgi impuls qarama-qarshi impulsning boshini buzgan bo'lsa, trend o'sha tomonga.
   Buzmagan bo'lsa, bu oddiy qaytish va trend o'zgarmaydi (0.618 gacha chuqur qaytish trendni buzmaydi).
3. **Fibo darajalari**: 0.236, 0.382, 0.5, 0.618, 0.786, 1.0 va TP uchun 1.272, 1.618.
4. **Signal shartlari** (hammasi majburiy):
   - M15 va M5 trendi bir xil;
   - narx M15 da golden zone (0.5-0.786) ga qaytgan (0.886 dan chuqur emas);
   - qaytish nuqtasi SNR zonasida (H4 yoki M15, kamida 2 marta qaytgan joy);
   - M1 da trend tomonga BOS bo'lgan (tasdiq).
5. **Kirish**: M1 yopilish narxi. **SL**: qaytish cho'qqisi ortida. **TP1**: B (0 daraja yoki yo'ldagi SNR),
   **TP2**: 1.272, **TP3**: 1.618. TP2 gacha kamida 1:2.

Ball (10 dan): asos 6 + H4 SNR 1 + zona 3+ marta 1 + chuqur qaytish (0.618+) yoki M5 golden zone 1 +
H4 EMA50 yo'nalishi mos 1. Standart chegara: 7 (`FIBO_MIN_SCORE`).
Signal bo'lmasa, bot nimani kutish kerakligini aniq yozadi (masalan, "M1 sham 2017.71 ustida yopilsa tasdiq").
Javob bilan M15 va M5 grafiklari keladi: Fibo darajalari, golden zone, SNR, kirish/SL/TP.
`fibo` har chaqirilganda 3 ta so'rov ketadi (M15, M5, M1). Twelve Data bepul rejasida daqiqasiga 8 ta so'rov bor.
Fibo signali faqat siz `fibo` deb yozganda chiqadi, avtomatik skanerga qo'shilmagan va hali backtest qilinmagan.

## Grafik rasm va skrinshot tahlili
- `rasm` (yoki `/rasm`): bot M15 grafikni chizadi: SNR zonalar, likvidlik, SMC signal bo'lsa kirish/SL/TP.
- `ict`: matnli tahlildan keyin ICT grafigi keladi (Osiyo/London qutilari, Midnight Open, FVG, OTE, sweep, MSS).
- **Skrinshot yuboring** (TradingView va h.k.): AI uni ICT/SMC bo'yicha o'qib tahlil qiladi.
  Rasm ostiga savol yozsangiz (masalan, "sotaymi?"), shu savolga ham javob beradi.
  Buning uchun Vercel'da `ANTHROPIC_API_KEY` kerak (console.anthropic.com, pullik, bitta tahlil taxminan 1-3 sent).
  Ixtiyoriy `AI_MODEL` bilan modelni o'zgartirasiz.

## O'rnatish
1. **Ma'lumot**: twelvedata.com da ro'yxatdan o'ting, bepul API kalit oling (`TWELVE_API_KEY`).
   Kalit bo'lmasa, bot Yahoo `GC=F` (fyuchers) dan foydalanadi: narxi spotdan biroz farq qiladi.
2. **Bot**: @BotFather da bot tokeni oling. O'z Telegram ID'ingizni @userinfobot dan bilib oling.
3. **Vercel > Settings > Environment Variables**:
   - `BOT_TOKEN`, `ADMIN_ID`, `WEBHOOK_SECRET` (uzun tasodifiy so'z, faqat harf/raqam/_/-)
   - `TWELVE_API_KEY`
   - ixtiyoriy: `CRON_SECRET`, `MIN_SCORE`, `MIN_RR`, `ENTRY_TIMEOUT`, `ICT_MIN_SCORE`, `FIBO_MIN_SCORE`
   - skrinshot tahlili uchun: `ANTHROPIC_API_KEY` (ixtiyoriy `AI_MODEL`)
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
**`signal`** (yoki `/signal`) SMC jonli tahlil | **`ict`** ICT tahlil + grafik | **`rasm`** grafik rasm | **`fibo`** Auto Fibo + SNR signal va grafik |
📷 skrinshot yuborish: AI tahlili | `/zones` SNR zonalar va likvidlik xaritasi |
`/backtest` SMC ni tarixda sinash | `/ictbacktest` ICT ni tarixda sinash | `/tarix` oxirgi signallar | `/sozlama`

## Ishga tushirishdan oldin
1. `/backtest` ni bosing va natijani o'qing (spred $0.30 hisobga olinadi).
2. 30-50 tadan kam savdo bo'lsa, natija ishonchli emas.
3. Kamida 2-4 hafta demo hisobda yoki juda kichik summada kuzating.

## Testlar
`python3 tests/test_smc.py` (SMC), `python3 tests/test_ict.py` (ICT), `python3 tests/test_bot.py` va
`python3 tests/test_bot_ict.py` (server, grafik va skrinshot buyruqlari),
`python3 tests/test_fibo.py` (Auto Fibo) va `python3 tests/test_bot_fibo.py` (`fibo` buyrug'i).
