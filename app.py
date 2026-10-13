"""Taiwan stock portfolio MVP. Python standard library only, SQLite storage."""
import csv
import base64
import hmac
import binascii
import io
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib import parse, request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('PORTFOLIO_DB', str(ROOT / 'portfolio.sqlite3')))
PORT = int(os.environ.get('PORT', '8765'))
HOST = os.environ.get('HOST', '127.0.0.1')
APP_USER = os.environ.get('APP_USER', '')
APP_PASSWORD = os.environ.get('APP_PASSWORD', '')
CLOUD_MODE = os.environ.get('CLOUD_MODE', '') == '1'
if CLOUD_MODE and (not APP_USER or len(APP_PASSWORD) < 12):
    raise SystemExit('雲端模式必須設定 APP_USER 與長度至少 12 字元的 APP_PASSWORD')
if CLOUD_MODE and not str(DB_PATH).startswith('/data/'):
    raise SystemExit('雲端模式須將 PORTFOLIO_DB 放在持久化磁碟 /data/ 下')
STATIC = ROOT / 'static'
FINMIND = 'https://api.finmindtrade.com/api/v4/data'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, symbol TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '', side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
 qty INTEGER NOT NULL CHECK(qty>0), price TEXT NOT NULL, fee TEXT NOT NULL DEFAULT '0',
 tax TEXT NOT NULL DEFAULT '0', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS prices (
 symbol TEXT NOT NULL, date TEXT NOT NULL, close TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'manual', PRIMARY KEY(symbol,date)
);
CREATE TABLE IF NOT EXISTS cashflows (
 id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
 amount TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
'''

def money(v):
    try:
        x = Decimal(str(v))
        if not x.is_finite(): raise ValueError()
        return x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('金額或價格格式錯誤')

def strmoney(x): return str(money(x))
def valid_date(s):
    if not isinstance(s,str) or date.fromisoformat(s).isoformat()!=s: raise ValueError('日期必須是 YYYY-MM-DD')
    return s

def valid_symbol(s):
    s=str(s).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]{4,8}',s): raise ValueError('台股代號須為 4–8 位英數字')
    return s

@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB_PATH)
    c.row_factory=sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()

def json_rows(rows): return [dict(x) for x in rows]

def positions_at(trades, cutoff=None):
    lots={}; realized=Decimal('0'); totals={}; names={}
    for t in trades:
        if cutoff and t['date']>cutoff: break
        s=t['symbol']; names[s]=t['name'] or names.get(s,s)
        q=lots.get(s,{'qty':0,'cost':Decimal('0')})['qty']
        c=lots.get(s,{'qty':0,'cost':Decimal('0')})['cost']
        qty=int(t['qty']); price=money(t['price']); fee=money(t['fee']); tax=money(t['tax'])
        if t['side']=='BUY':
            lots[s]={'qty':q+qty,'cost':c+price*qty+fee+tax}
        else:
            if qty>q: raise ValueError(f'{t["date"]} {s} 賣出股數超過庫存（當時持有 {q} 股）')
            removed=c*qty/q
            realized += price*qty-fee-tax-removed
            lots[s]={'qty':q-qty,'cost':c-removed if qty<q else Decimal('0')}
    return lots,realized,names

def validate_trade_history(conn):
    rows=conn.execute('SELECT * FROM trades ORDER BY date,id').fetchall()
    positions_at(rows)

def snapshot(conn):
    trades=json_rows(conn.execute('SELECT * FROM trades ORDER BY date,id'))
    ps=json_rows(conn.execute('SELECT * FROM prices ORDER BY date'))
    flows=json_rows(conn.execute('SELECT * FROM cashflows ORDER BY date,id'))
    return trades,ps,flows

def calculate(conn):
    trades,ps,flows=snapshot(conn)
    today=date.today().isoformat()
    all_dates=sorted({r['date'] for r in trades+ps+flows if r['date']<=today})
    if not all_dates:
        return {'summary':{'equity':0,'market_value':0,'cash':0,'day_pnl':None,'day_return_pct':None,'total_pnl':0,'total_return_pct':None,'realized':0,'unrealized':0,'first_date':None,'last_date':None},'holdings':[],'history':[],'warnings':[]}
    price_by_sym={}
    for p in ps:
        if p['date']<=today: price_by_sym.setdefault(p['symbol'],[]).append(p)
    latest={s:v[-1] for s,v in price_by_sym.items() if v}
    lots,realized,names=positions_at(trades,today)
    total_buys=sum((money(t['price'])*int(t['qty'])+money(t['fee'])+money(t['tax']) for t in trades if t['side']=='BUY' and t['date']<=today),Decimal(0))
    total_sells=sum((money(t['price'])*int(t['qty'])-money(t['fee'])-money(t['tax']) for t in trades if t['side']=='SELL' and t['date']<=today),Decimal(0))
    total_flow=sum((money(f['amount']) for f in flows if f['date']<=today),Decimal(0))
    cash=total_flow-total_buys+total_sells
    holdings=[]; warnings=[]; mv=Decimal(0); unreal=Decimal(0)
    for s,v in sorted(lots.items()):
        if v['qty']==0: continue
        last=latest.get(s)
        price=money(last['close']) if last else None
        value=price*v['qty'] if price is not None else None
        if value is None: warnings.append(f'{s} 沒有收盤價；未計入持股市值及損益')
        else: mv+=value; unreal+=value-v['cost']
        holdings.append({'symbol':s,'name':names.get(s,s),'qty':v['qty'],'avg_cost':float(v['cost']/v['qty']),
         'cost':float(v['cost']),'close':float(price) if price is not None else None,
         'price_date':last['date'] if last else None,'market_value':float(value) if value is not None else None,
         'unrealized_pnl':float(value-v['cost']) if value is not None else None,
         'unrealized_pct':float((value-v['cost'])/v['cost']*100) if value is not None and v['cost'] else None})
    # Each day with a price observation or transaction / capital flow, with carry-forward prices.
    events=sorted(set(all_dates))
    history=[]; cumulative_factor=Decimal(1)
    last_equity=None
    for d in events:
        lt,_,_=positions_at(trades,d)
        pmap={s:next((p for p in reversed(arr) if p['date']<=d),None) for s,arr in price_by_sym.items()}
        partial=False; value=Decimal(0)
        for s,v in lt.items():
            if v['qty']:
                p=pmap.get(s)
                if p is None: partial=True
                else: value += money(p['close'])*v['qty']
        if partial: continue  # never produce misleading history with missing prices
        cb=sum((money(t['price'])*t['qty']+money(t['fee'])+money(t['tax']) for t in trades if t['date']<=d and t['side']=='BUY'),Decimal(0))
        cs=sum((money(t['price'])*t['qty']-money(t['fee'])-money(t['tax']) for t in trades if t['date']<=d and t['side']=='SELL'),Decimal(0))
        fsum=sum((money(f['amount']) for f in flows if f['date']<=d),Decimal(0))
        equity=fsum-cb+cs+value
        daily_flows=sum((money(f['amount']) for f in flows if f['date']==d),Decimal(0))
        daily_pnl=(equity-last_equity-daily_flows) if last_equity is not None else (equity-daily_flows)
        # Return is approximate when flows occur on the same date; use pre-flow capital.
        base=last_equity if last_equity is not None else daily_flows
        ret=daily_pnl/base*100 if base>0 else None
        if ret is not None: cumulative_factor *= (1+ret/100)
        history.append({'date':d,'equity':float(equity),'day_pnl':float(daily_pnl),
           'day_return_pct':float(ret) if ret is not None else None,'cumulative_return_pct':float((cumulative_factor-1)*100)})
        last_equity=equity
    total_pnl=cash+mv-total_flow
    # Simple return uses net contributions as denominator; not a valid time-weighted or money-weighted return when flows vary.
    simple_return=total_pnl/total_flow*100 if total_flow>0 else None
    lastday=history[-1] if history else None
    if len(history)==1: lastday=None
    if any(f['date']<=today for f in flows):
        pass
    else: warnings.append('尚未輸入入金；現金餘額可能為負，總報酬率無法計算')
    if cash<0: warnings.append('現金餘額為負；請確認是否有漏記入金或交易')
    if len(history)>1 and any(f['date']==history[-1]['date'] for f in flows): warnings.append('同日有出入金：單日報酬率採近似計算')
    if not latest and holdings: warnings.append('尚無股價，請更新行情或手動輸入收盤價')
    return {'summary':{'equity':float(cash+mv),'market_value':float(mv),'cash':float(cash),
        'day_pnl':lastday['day_pnl'] if lastday else None,
        'day_return_pct':lastday['day_return_pct'] if lastday else None,
        'total_pnl':float(total_pnl),'total_return_pct':float(simple_return) if simple_return is not None else None,
        'realized':float(realized),'unrealized':float(unreal),'first_date':all_dates[0],
        'last_date':history[-1]['date'] if history else None},'holdings':holdings,'history':history,'warnings':warnings}

def insert_trade(conn,row):
    d=valid_date(row.get('date','')); symbol=valid_symbol(row.get('symbol',''))
    side=str(row.get('side','')).strip().upper()
    if side not in ('BUY','SELL'): raise ValueError('方向請填 BUY 或 SELL')
    qty=int(row.get('qty',''))
    if qty<=0: raise ValueError('股數必須大於 0')
    price=money(row.get('price'))
    if price<=0: raise ValueError('成交價必須大於 0')
    fee=money(row.get('fee',0) or 0); tax=money(row.get('tax',0) or 0)
    if fee<0 or tax<0: raise ValueError('費用與稅金不可小於 0')
    conn.execute('INSERT INTO trades(date,symbol,name,side,qty,price,fee,tax,note) VALUES (?,?,?,?,?,?,?,?,?)',
     (d,symbol,str(row.get('name','')).strip()[:80],side,qty,str(price),str(fee),str(tax),str(row.get('note',''))[:500]))
    validate_trade_history(conn)

def insert_price(conn,row,source='manual'):
    d=valid_date(row.get('date','')); symbol=valid_symbol(row.get('symbol','')); close=money(row.get('close'))
    if close<=0: raise ValueError('收盤價必須大於 0')
    conn.execute('INSERT INTO prices(symbol,date,close,source) VALUES(?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET close=excluded.close,source=excluded.source',
       (symbol,d,str(close),source))

def fetch_prices(conn):
    syms=[r['symbol'] for r in conn.execute('SELECT DISTINCT symbol FROM trades')]
    if not syms: raise ValueError('請先輸入至少一筆交易')
    first=conn.execute('SELECT MIN(date) d FROM trades').fetchone()['d']
    earliest=(date.fromisoformat(first)-timedelta(days=8)).isoformat()
    token=os.environ.get('FINMIND_TOKEN','')
    successes=[]; errors=[]
    for s in syms:
        query=parse.urlencode({'dataset':'TaiwanStockPrice','data_id':s,'start_date':earliest})
        req=request.Request(FINMIND+'?'+query,headers=({'Authorization':'Bearer '+token} if token else {}) | {'User-Agent':'PortfolioMVP/1.0'})
        try:
            with request.urlopen(req,timeout=25) as resp: obj=json.load(resp)
            if obj.get('status')!=200 or not isinstance(obj.get('data'),list): raise ValueError(str(obj.get('msg','API 回應異常')))
            count=0
            for p in obj['data']:
                try: insert_price(conn,{'symbol':s,'date':p['date'],'close':p['close']},'FinMind'); count+=1
                except (KeyError,ValueError): continue
            successes.append(f'{s}: {count} 筆')
        except Exception as e: errors.append(f'{s}: {e}')
    return {'updated':successes,'errors':errors}

class Handler(BaseHTTPRequestHandler):
    def is_authorized(self):
        if not APP_USER and not APP_PASSWORD:
            return not CLOUD_MODE
        header = self.headers.get('Authorization', '')
        if not header.startswith('Basic '): return False
        try:
            credentials = base64.b64decode(header[6:], validate=True).decode('utf-8')
            user, password = credentials.split(':', 1)
        except (ValueError, UnicodeError, binascii.Error): return False
        return hmac.compare_digest(user, APP_USER) and hmac.compare_digest(password, APP_PASSWORD)
    def require_auth(self):
        if self.is_authorized(): return False
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Portfolio Studio", charset="UTF-8"')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return True
    def send(self,status=200,payload=None,content_type='application/json; charset=utf-8'):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if content_type.startswith('application/json') else payload
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>3_000_000: raise ValueError('上傳資料不可超過 3 MB')
        return json.loads(self.rfile.read(n).decode('utf-8'))
    def do_GET(self):
        if self.require_auth(): return
        try:
            if self.path=='/':
                self.send(200,(STATIC/'index.html').read_bytes(),'text/html; charset=utf-8'); return
            if self.path=='/static/app.js':
                self.send(200,(STATIC/'app.js').read_bytes(),'text/javascript; charset=utf-8'); return
            if self.path=='/static/style.css':
                self.send(200,(STATIC/'style.css').read_bytes(),'text/css; charset=utf-8'); return
            with db() as conn:
                if self.path=='/api/dashboard': self.send(payload=calculate(conn)); return
                if self.path=='/api/trades': self.send(payload=json_rows(conn.execute('SELECT * FROM trades ORDER BY date DESC,id DESC')));return
                if self.path=='/api/cashflows': self.send(payload=json_rows(conn.execute('SELECT * FROM cashflows ORDER BY date DESC,id DESC')));return
                if self.path=='/api/prices': self.send(payload=json_rows(conn.execute('SELECT * FROM prices ORDER BY date DESC,symbol LIMIT 500')));return
            self.send(404,{'error':'找不到路徑'})
        except Exception as e: self.send(400,{'error':str(e)})
    def do_POST(self):
        if self.require_auth(): return
        try:
            data=self.body(); action=self.path
            with db() as conn:
                if action=='/api/trades': insert_trade(conn,data); self.send(payload={'ok':True}); return
                if action=='/api/prices': insert_price(conn,data); self.send(payload={'ok':True}); return
                if action=='/api/cashflows':
                    d=valid_date(data.get('date','')); amount=money(data.get('amount'))
                    if not amount: raise ValueError('金額不可為零')
                    conn.execute('INSERT INTO cashflows(date,amount,note) VALUES(?,?,?)',(d,str(amount),str(data.get('note',''))[:500]))
                    self.send(payload={'ok':True});return
                if action=='/api/import':
                    raw=data.get('csv',''); rows=list(csv.DictReader(io.StringIO(raw.lstrip('\ufeff'))))
                    if len(rows)>3000: raise ValueError('單次上傳最多 3000 筆')
                    if not rows: raise ValueError('CSV 沒有交易資料')
                    # Outer connection transaction rollback on invalid input.
                    for row in rows: insert_trade(conn,row)
                    self.send(payload={'ok':True,'count':len(rows)});return
                if action=='/api/sync': self.send(payload=fetch_prices(conn));return
            self.send(404,{'error':'找不到路徑'})
        except Exception as e: self.send(400,{'error':str(e)})
    def do_DELETE(self):
        if self.require_auth(): return
        try:
            m=re.fullmatch(r'/api/(trades|cashflows|prices)/(\d+|[0-9A-Z]{4,8}/\d{4}-\d{2}-\d{2})',self.path)
            if not m: self.send(404,{'error':'無效路徑'});return
            table,key=m.groups()
            with db() as conn:
                if table=='prices':
                    s,d=key.split('/');conn.execute('DELETE FROM prices WHERE symbol=? AND date=?',(s,d))
                else:
                    conn.execute('DELETE FROM '+table+' WHERE id=?',(int(key),))
                    if table=='trades': validate_trade_history(conn)
            self.send(payload={'ok':True})
        except Exception as e: self.send(400,{'error':str(e)})
    def log_message(self,fmt,*args): pass

if __name__=='__main__':
    print('投資績效 MVP 已啟動：http://'+HOST+':'+str(PORT))
    print('資料庫：'+str(DB_PATH))
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
