# PolyHedge

Локальный стол для сверки **Polymarket** с букмекерской линией. Это не клон SpreadCore, не облачный сервис и не «безрисковый станок».

Считает вилку по честной формуле комиссий Polymarket, показывает живой стакан и break-even кэф. Ключи кошелька никуда не отправляются.

## Что умеет

- Сканер спортивных событий Polymarket (Gamma API) + стакан CLOB
- Вторые ноги с **публичных** API, не с парсера казино:
  - **SX.bet** — moneyline / winner + кэфы из публичных ордеров
  - **Smarkets** — winner / FT result
  - **Kalshi** — серии GAME/MATCH (NFL, MLB, EPL, UCL…)
  - **The Odds API** (ключ) — Pinnacle, 1xBet, Betfair, William Hill и другие легальные книги
  - ручной кэф для Stake / Empire / Roobet и всего, у чего нет открытого API
- Калькулятор двух ног: шейры на Polymarket ↔ десятичный кэф на БК
- Taker fee: `shares × fee_rate × p × (1 − p)` (спорт обычно `0.05`)
- Maker без комиссии; rebate только как оценка, не как гарантия

## Чего нет и не будет

- Скрейп Stake, Empire, Roobet, Mellstroy и прочих казино
- Хранение private key на чужом сервере
- Обход KYC, накрутка мультиаккаунтов, «держать аккаунт в минусе»
- Надпись risk-free: плюс есть только если **обе ноги реально исполнились**

SpreadCore берёт деньги за закрытый парсер десятка серых контор и за то, что вы отдаёте им ключ. Здесь другая модель: локальный софт + публичные API.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # по желанию ODDS_API_KEY
uvicorn polyhedge.server:app --host 127.0.0.1 --port 8787
```

Откройте `http://127.0.0.1:8787`.

Тесты математики:

```bash
pytest -q
```

## Как читать ROI

1. Берёте исход на Polymarket по цене `p` (лимитка = без taker fee).
2. Противоположный исход на БК по десятичному кэфу `O`.
3. Ставка на БК `S = shares / O`, чтобы выплаты совпали.
4. Профит `shares − (shares·p + fee) − S`.

Если кэф БК ниже break-even — вилки нет, это направленный риск.

## Odds API

Бесплатный ключ: https://the-odds-api.com  
Впишите в `.env`:

```
ODDS_API_KEY=...
```

Без ключа стол всё равно тянет SX.bet, Smarkets и Kalshi. Stake и казино — только ручной кэф.

## Важно

Арбитраж, ставки и prediction markets регулируются по-разному в разных странах. Букмекеры режут +EV. Инструмент не исполняет ставки на БК за вас.
