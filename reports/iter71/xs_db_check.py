import asyncio, os, time
from dotenv import load_dotenv
load_dotenv("/app/.env", override=False)
os.environ["DB_HOST"] = "postgres"
from src.config import AppConfig, XS_UNIVERSE, TIMEFRAME_MS, load_params
from src.db import connection as db_conn
from src.db import writer as db
from src.signal.patterns import cross_section_entry

async def main():
    cfg = AppConfig.from_mapping(os.environ)
    await db_conn.init_pool(cfg)
    try:
        step = TIMEFRAME_MS["5m"]
        now = (int(time.time() * 1000) // step) * step - 2 * step  # a candle every bot has closed
        lb = 12 * step
        t0 = time.perf_counter()
        closes = await db.load_closes_at(XS_UNIVERSE, "5m", [now, now - lb, now - step, now - step - lb], "dev")
        ms = (time.perf_counter() - t0) * 1000
        snap = lambda ts: {p: (closes[(p, ts)], closes[(p, ts - lb)]) for p in XS_UNIVERSE if (p, ts) in closes and (p, ts - lb) in closes}
        print(f"rows={len(closes)} (want 40) query={ms:.1f} ms")
        hits = []
        for back in range(24):
            t = now - back * step
            c2 = await db.load_closes_at(XS_UNIVERSE, "5m", [t, t - lb, t - step, t - step - lb], "dev")
            sn = lambda ts: {p: (c2[(p, ts)], c2[(p, ts - lb)]) for p in XS_UNIVERSE if (p, ts) in c2 and (p, ts - lb) in c2}
            e = {p.split("/")[0]: cross_section_entry(p, sn(t), sn(t - step), 2) for p in XS_UNIVERSE}
            hits.append({k: v for k, v in e.items() if v})
        print("entries over last 24 candles:", sum(len(h) for h in hits), "e.g.", [h for h in hits if h][:4])
    finally:
        await db_conn.close_pool()

asyncio.run(main())
