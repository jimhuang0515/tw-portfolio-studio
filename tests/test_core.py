import os,sys,tempfile,unittest
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import app

class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.old=app.DB_PATH;app.DB_PATH=app.Path(self.t.name)/'test.db'
    def tearDown(self):app.DB_PATH=self.old;self.t.cleanup()
    def test_buy_sell_fee_realized(self):
        with app.db() as c:
            c.execute("INSERT INTO cashflows(date,amount) VALUES('2026-10-01','100000')")
            app.insert_trade(c,dict(date='2026-10-01',symbol='2330',side='BUY',qty=100,price=500,fee=20))
            app.insert_trade(c,dict(date='2026-10-02',symbol='2330',side='SELL',qty=40,price=550,fee=10,tax=30))
            app.insert_price(c,dict(date='2026-10-01',symbol='2330',close=500))
            app.insert_price(c,dict(date='2026-10-02',symbol='2330',close=550))
            d=app.calculate(c)
            self.assertEqual(d['holdings'][0]['qty'],60)
            self.assertAlmostEqual(d['summary']['realized'],1952)
            self.assertAlmostEqual(d['summary']['total_pnl'],4940)
            self.assertAlmostEqual(d['summary']['equity'],104940)
    def test_no_naked_sell(self):
        with self.assertRaises(ValueError):
            with app.db() as c:
                app.insert_trade(c,dict(date='2026-10-01',symbol='2330',side='SELL',qty=1,price=500))
        with app.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM trades').fetchone()[0],0)
    def test_missing_price_skips_history(self):
        with app.db() as c:
            c.execute("INSERT INTO cashflows(date,amount) VALUES('2026-10-01','100000')")
            app.insert_trade(c,dict(date='2026-10-01',symbol='2330',side='BUY',qty=100,price=500))
            self.assertEqual(len(app.calculate(c)['history']),0)
            app.insert_price(c,dict(date='2026-10-02',symbol='2330',close=500))
            self.assertTrue(app.calculate(c)['history'])
if __name__=='__main__':unittest.main()
