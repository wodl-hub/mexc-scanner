import ccxt
import time
import requests
import os
from datetime import datetime
from collections import defaultdict
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

def send_telegram_message(text, photo_path=None):
    TOKEN = "8330642425:AAGJAMBslyLCDbcb08SzXMvgFGWM17b5xGk"
    CHAT_ID = "-1003733992231"
    url_text = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    url_photo = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    if photo_path and os.path.exists(photo_path):
        try:
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                data = {
                    'chat_id': CHAT_ID,
                    'caption': text,
                    'parse_mode': 'MarkdownV2'
                }
                response = requests.post(url_photo, data=data, files=files, timeout=25)
            if response.status_code == 200:
                print(f"→ Telegram: фото + текст отправлено ({text[:50]}...)")
                return True
            else:
                print(f"Ошибка отправки фото ({response.status_code}): {response.text[:120]}")
        except Exception as e:
            print(f"Ошибка при отправке фото: {e}")
    # Fallback — только текст
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2"
    }
    try:
        response = requests.post(url_text, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"→ Telegram: текст отправлено ({text[:50]}...)")
            return True
        else:
            print(f"Ошибка TG текст ({response.status_code}): {response.text[:100]}")
            return False
    except Exception as e:
        print(f"Не удалось отправить текст в TG: {e}")
        return False

def generate_chart_screenshot(exchange, symbol, minutes_back=240):
    try:
        since = exchange.milliseconds() - minutes_back * 60 * 1000
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', since=since, limit=500)
    
        if len(ohlcv) < 20:
            print(f"Мало данных для графика {symbol} ({len(ohlcv)} свечей)")
            return None
    
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
    
        df['volume_usdt'] = df['volume'] * df['close']
    
        temp_path = f"temp_chart_{symbol.replace('/', '_')}.png"
    
        def volume_formatter(x, pos):
            if x >= 1e6:
                return f'{x / 1e6:.1f}M $'
            elif x >= 1e3:
                return f'{x / 1e3:.0f}K $'
            else:
                return f'{int(x)} $'
    
        def price_formatter(x, pos):
            if x >= 100:
                return f'{int(x)}'
            elif x >= 10:
                return f'{x:.1f}'
            elif x >= 1:
                return f'{x:.2f}'
            elif x >= 0.1:
                return f'{x:.3f}'
            elif x >= 0.01:
                return f'{x:.4f}'
            else:
                return f'{x:.6f}'
    
        colors = ['#26a69a' if o <= c else '#ef5350'
                  for o, c in zip(df['open'], df['close'])]
    
        volume_plot = mpf.make_addplot(
            df['volume_usdt'],
            panel=1,
            type='bar',
            color=colors,
            alpha=0.8,
            ylabel="Volume USDT"
        )
    
        fig, axlist = mpf.plot(
            df,
            type='candle',
            style='yahoo',
            title=f"{symbol} • 5m • последние {minutes_back//60} ч",
            ylabel="Price (USDT)",
            addplot=volume_plot,
            volume=False,
            figsize=(12, 9.2),
            panel_ratios=(3.5, 1),
            returnfig=True,
            tight_layout=False
        )
    
        axlist[0].grid(True, axis='y', linestyle='--', alpha=0.3, color='lightgray')
        axlist[0].grid(False, axis='x')
        axlist[1].grid(False)
      
        fig.subplots_adjust(top=0.88, hspace=0.48)
        axlist[1].set_facecolor('#ffffff')
    
        axlist[0].yaxis.set_major_formatter(FuncFormatter(price_formatter))
        axlist[1].yaxis.set_major_formatter(FuncFormatter(volume_formatter))
     
        for ax in axlist:
            ax.yaxis.tick_left()
            ax.yaxis.set_label_position("left")
    
        fig.savefig(temp_path, dpi=140, bbox_inches='tight', facecolor='white')
        plt.close(fig)
    
        return temp_path
    except Exception as e:
        print(f"Ошибка при создании графика {symbol}: {e}")
        return None

print("Сканер повторяющихся объёмов среди ВСЕХ сделок на low-volume спотовых парах MEXC (без фьючерса)")
exchange = ccxt.mexc({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'},
})

# Параметры
WINDOW_SECONDS = 60
MIN_REPEAT_COUNT = 6
MIN_USDT_VALUE = 0.1
ROUND_TO = 2
LOG_FILE = "repeat_volume_log.txt"
ALERT_COOLDOWN_LONG = 10 * 3600  # 10 часов

volume_cache = defaultdict(list)
last_alert_time = defaultdict(float)  # ключ — symbol

# ─── Исключаем пары с фьючерсом ───
print("Загружаю список фьючерсных USDT-перпетуалов...")
futures_bases = set()
try:
    exchange.options['defaultType'] = 'swap'
    futures_markets = exchange.load_markets(reload=True)
    for symbol in futures_markets:
        if symbol.endswith('/USDT:USDT') or '_USDT' in symbol:
            base = futures_markets[symbol]['base'].upper()
            futures_bases.add(base)
    print(f"Найдено {len(futures_bases)} базовых активов с фьючерсами")
except Exception as e:
    print(f"Ошибка загрузки фьючерсов: {e}")
finally:
    exchange.options['defaultType'] = 'spot'

# ─── Фильтр спотовых low-volume пар ───
print("Загружаю спотовые 24h тикеры и фильтрую...")
tickers = exchange.fetch_tickers()
low_volume_symbols = []
for symbol, ticker in tickers.items():
    if not symbol.endswith('/USDT'):
        continue
    base = symbol.split('/')[0].upper()
    if base in futures_bases:
        continue
    if (
        'quoteVolume' in ticker and
        ticker['quoteVolume'] is not None and
        1000 < float(ticker['quoteVolume']) < 1_000_000
    ):
        low_volume_symbols.append(symbol)

print(f"Найдено {len(low_volume_symbols)} подходящих спотовых пар.")
if low_volume_symbols:
    print("Первые 10:", low_volume_symbols[:10])
else:
    print("Пар не найдено.")
    exit()

# ─── Основной цикл ───
while True:
    try:
        for symbol in low_volume_symbols:
            try:
                trades = exchange.fetch_trades(
                    symbol,
                    limit=1000,
                    params={'recvWindow': 30000}
                )
           
                if not trades:
                    continue
           
                now = time.time()
           
                for trade in trades:
                    usdt_value = trade.get('cost') or (trade['amount'] * trade['price'])
                    if usdt_value < MIN_USDT_VALUE:
                        continue
               
                    rounded = round(usdt_value, ROUND_TO)
                    ts = trade['timestamp'] / 1000
               
                    key = (symbol, rounded)
                    volume_cache[key].append(ts)
               
                    volume_cache[key] = [t for t in volume_cache[key] if now - t < WINDOW_SECONDS]
               
                    # Мягкая очистка кэша — удаляем только старые неактивные ключи
                    if len(volume_cache) > 40000:
                        to_remove = []
                        for k, times in list(volume_cache.items()):
                            if not times or (now - max(times)) > 3600:  # не обновлялся более 1 часа
                                to_remove.append(k)
                        
                        for k in to_remove:
                            del volume_cache[k]
                        
                        if to_remove:
                            print(f"[{time.strftime('%H:%M:%S')}] Удалено старых ключей: {len(to_remove)}, осталось: {len(volume_cache)}")
               
                    count = len(volume_cache[key])
                    if count >= MIN_REPEAT_COUNT:
                        if now - last_alert_time[symbol] >= ALERT_COOLDOWN_LONG:
                            pair_clean = symbol.replace('/', '')
                       
                            msg = f"🔥 **MEXC** `{pair_clean}`"
                       
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            clean_msg = f"🔥 MEXC {pair_clean}"
                            print(f"[{now_str}] {clean_msg}")
                            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                                f.write(f"[{now_str}] {clean_msg}\n")
                       
                            photo_path = generate_chart_screenshot(exchange, symbol, minutes_back=240)
                       
                            send_telegram_message(msg, photo_path=photo_path)
                       
                            if photo_path and os.path.exists(photo_path):
                                try:
                                    os.remove(photo_path)
                                except:
                                    pass
                       
                            last_alert_time[symbol] = now
       
            except ccxt.RateLimitExceeded:
                print(f"Rate limit на {symbol}, пауза...")
                time.sleep(10)
                continue
            except Exception as inner:
                print(f"[{symbol}] ошибка: {inner}")
                time.sleep(2)
                continue
   
        print(f"Цикл завершён → {time.strftime('%H:%M:%S')}")
        time.sleep(3)
    except Exception as e:
        print("Глобальная ошибка:", e)
        time.sleep(15)