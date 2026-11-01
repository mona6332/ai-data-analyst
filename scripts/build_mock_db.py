"""임시(목업) 데이터 웨어하우스 생성 스크립트.

분석 허브 아키텍처를 그대로 흉내 냅니다.
  원천(카페24 주문/회원, 웹트래킹, ERP 상품, 프로모션, 광고비)
    -> SILVER (silver_*)  : 수집 원본 정제본
    -> GOLD   (gold_*)    : 주문·품목 표준 뷰
    -> G1     (g1_*)      : 팩트·디멘션
    -> 도메인 (domain_*)  : 프로모션 캘린더, 계절성 트렌드
AI 에이전트는 GOLD·G1·도메인만 읽습니다.

데모용으로 일부러 넣어둔 '특이사항':
  1) 최근 3주 META 광고비는 늘었는데 ROAS 하락 (광고 효율 점검)
  2) '린넨 오버핏 셔츠'(P007) 10월 반품률 급증 (상품 이슈 점검)
  3) 신규 고객 증가, 하지만 재구매율 하락 (고객 유지 점검)
  4) 추석 프로모션 효과가 종료 직후 급감 (프로모션 사후 분석)
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START = date(2026, 6, 1)
END = date(2026, 10, 27)  # 데이터 기준일

CATEGORIES = {
    "상의": ["린넨 셔츠", "옥스포드 셔츠", "반팔 티셔츠", "니트 가디건", "후드 티셔츠", "맨투맨", "린넨 오버핏 셔츠", "스트라이프 셔츠"],
    "하의": ["와이드 슬랙스", "데님 팬츠", "조거 팬츠", "치노 팬츠", "카고 팬츠", "롱 스커트"],
    "아우터": ["트렌치 코트", "울 코트", "경량 패딩", "블레이저", "가죽 자켓"],
    "잡화": ["캔버스 백", "볼캡", "울 머플러", "가죽 벨트", "양말 3팩"],
    "신발": ["스니커즈", "로퍼", "첼시 부츠", "슬리퍼"],
}
CHANNELS = {  # channel: (세션 비중, 전환율)
    "META": (0.30, 0.020),
    "GOOGLE": (0.22, 0.025),
    "NAVER": (0.20, 0.030),
    "KAKAO": (0.10, 0.022),
    "ORGANIC": (0.13, 0.028),
    "DIRECT": (0.05, 0.040),
}
CAMPAIGNS = [
    ("C01", "META", "META_가을신상_전환", "conversion"),
    ("C02", "META", "META_리타겟팅", "retargeting"),
    ("C03", "GOOGLE", "GOOGLE_검색_브랜드", "search"),
    ("C04", "GOOGLE", "GOOGLE_PMAX", "conversion"),
    ("C05", "NAVER", "NAVER_파워링크", "search"),
    ("C06", "NAVER", "NAVER_쇼핑검색", "shopping"),
    ("C07", "KAKAO", "KAKAO_비즈보드", "awareness"),
    ("C08", "ORGANIC", "ORGANIC", "none"),
    ("C09", "DIRECT", "DIRECT", "none"),
]
PROMOTIONS = [
    ("PR01", "여름 시즌오프", "2026-07-20", "2026-07-31", 0.30, "SUMMER30", "상의"),
    ("PR02", "추석 감사 세일", "2026-09-21", "2026-09-28", 0.20, "CHUSEOK20", None),
    ("PR03", "가을 아우터 위크", "2026-10-13", "2026-10-19", 0.15, "OUTER15", "아우터"),
]


def build_products(rng):
    rows, i = [], 1
    for cat, names in CATEGORIES.items():
        base = {"상의": 39000, "하의": 49000, "아우터": 129000, "잡화": 25000, "신발": 79000}[cat]
        for n in names:
            price = int(round(base * rng.uniform(0.7, 1.5), -3))
            rows.append(dict(product_id=f"P{i:03d}", product_name=n, category=cat, brand="OURBRAND",
                             list_price=price, cost_price=int(price * rng.uniform(0.35, 0.5)),
                             launched_at=str(START - timedelta(days=int(rng.integers(30, 400))))))
            i += 1
    return pd.DataFrame(rows)


def promo_on(d):
    for p in PROMOTIONS:
        if p[2] <= str(d) <= p[3]:
            return p
    return None


def main(out: Path):
    rng = np.random.default_rng(SEED)
    products = build_products(rng)
    camp_by_ch = {}
    for c in CAMPAIGNS:
        camp_by_ch.setdefault(c[1], []).append(c[0])

    members, orders, items, sessions_agg, ads = [], [], [], [], []
    member_seq, order_seq = 0, 0
    active_members: list[tuple[str, date]] = []
    days = pd.date_range(START, END).date

    for d in days:
        dow_factor = 1.15 if d.weekday() in (5, 6) else 1.0
        season = 1.0 + 0.25 * ((d - START).days / (END - START).days)
        promo = promo_on(d)
        promo_boost = 1.8 if promo else 1.0
        # 추석 프로모션 종료 직후 수요 급감(선구매 효과)
        if date(2026, 9, 29) <= d <= date(2026, 10, 5):
            promo_boost = 0.7
        late = d >= END - timedelta(days=21)

        total_sessions = int(rng.normal(4200, 250) * dow_factor * season * (1.3 if promo else 1.0))
        for ch, (share, cvr) in CHANNELS.items():
            ch_sessions = int(total_sessions * share * rng.uniform(0.9, 1.1))
            if ch == "META" and late:
                ch_sessions = int(ch_sessions * 1.35)  # 광고비 증액 -> 유입은 늘었지만
                cvr = cvr * 0.55                      # 전환율 하락
            n_orders = rng.poisson(ch_sessions * cvr * promo_boost / 1.3)
            camps = camp_by_ch[ch]
            for camp in camps:
                if ch in ("ORGANIC", "DIRECT"):
                    continue
                spend_base = {"META": 900000, "GOOGLE": 650000, "NAVER": 550000, "KAKAO": 300000}[ch] / len(camps)
                spend = spend_base * rng.uniform(0.85, 1.15) * (1.6 if (ch == "META" and late) else 1.0)
                cpm = rng.uniform(4000, 7000)
                impressions = int(spend / cpm * 1000)
                clicks = int(impressions * rng.uniform(0.008, 0.015))
                ads.append(dict(date=str(d), channel=ch, campaign_id=camp, impressions=impressions,
                                clicks=clicks, spend=int(spend)))
            sessions_agg.append((str(d), ch, ch_sessions, camps))

            for _ in range(n_orders):
                # 재구매 vs 신규. 신규 고객 유입은 후반부로 갈수록 증가, 재구매 확률은 감소
                progress = (d - START).days / (END - START).days
                repurchase_p = 0.42 - 0.20 * progress
                if active_members and rng.random() < repurchase_p:
                    mid = active_members[int(rng.integers(len(active_members)))][0]
                else:
                    member_seq += 1
                    mid = f"M{member_seq:05d}"
                    members.append(dict(member_id=mid, joined_at=str(d),
                                        grade="WELCOME", gender=rng.choice(["F", "M"], p=[0.62, 0.38]),
                                        age_band=rng.choice(["20s", "30s", "40s", "50s+"], p=[0.35, 0.38, 0.19, 0.08]),
                                        signup_channel=ch))
                    active_members.append((mid, d))
                order_seq += 1
                oid = f"O{order_seq:07d}"
                camp = camps[int(rng.integers(len(camps)))]
                n_items = int(rng.choice([1, 1, 1, 2, 2, 3]))
                total, disc_total = 0, 0
                coupon = None
                for _ in range(n_items):
                    p = products.iloc[int(rng.integers(len(products)))]
                    # 가을이 될수록 아우터 판매 증가
                    if d.month >= 9 and rng.random() < 0.25:
                        p = products[products.category == "아우터"].sample(1, random_state=int(rng.integers(1e9))).iloc[0]
                    qty = int(rng.choice([1, 1, 1, 2]))
                    amt = int(p.list_price * qty)
                    disc = 0
                    if promo and (promo[6] is None or promo[6] == p.category):
                        disc = int(amt * promo[4])
                        coupon = promo[5]
                    ret_p = 0.05
                    if p.product_id == "P007" and d >= date(2026, 10, 1):
                        ret_p = 0.32  # 린넨 오버핏 셔츠 반품 급증
                    returned = qty if rng.random() < ret_p else 0
                    items.append(dict(order_id=oid, product_id=p.product_id, qty=qty, unit_price=int(p.list_price),
                                      item_amount=amt, discount_amount=disc, returned_qty=returned))
                    total += amt
                    disc_total += disc
                ts = datetime(d.year, d.month, d.day, int(rng.integers(0, 24)), int(rng.integers(0, 60)))
                orders.append(dict(order_id=oid, member_id=mid, order_ts=ts.isoformat(sep=" "), channel=ch,
                                   campaign_id=camp, status="PAID", total_amount=total,
                                   discount_amount=disc_total, coupon_code=coupon))

    members_df = pd.DataFrame(members)
    members_df.loc[members_df.sample(frac=0.15, random_state=1).index, "grade"] = "SILVER"
    members_df.loc[members_df.sample(frac=0.05, random_state=2).index, "grade"] = "GOLD"
    orders_df, items_df = pd.DataFrame(orders), pd.DataFrame(items)
    ads_df = pd.DataFrame(ads)
    sess_rows = []
    for d, ch, n, camps in sessions_agg:
        for c in camps:
            sess_rows.append(dict(session_date=d, channel=ch, campaign_id=c, sessions=n // len(camps),
                                  pageviews=int(n / len(camps) * rng.uniform(3, 5))))
    sessions_df = pd.DataFrame(sess_rows)
    promos_df = pd.DataFrame(PROMOTIONS, columns=["promo_id", "promo_name", "start_date", "end_date",
                                                  "discount_rate", "coupon_code", "target_category"])
    camps_df = pd.DataFrame(CAMPAIGNS, columns=["campaign_id", "channel", "campaign_name", "objective"])

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    con = sqlite3.connect(out)
    # ---- SILVER
    orders_df.to_sql("silver_orders", con, index=False)
    items_df.to_sql("silver_order_items", con, index=False)
    members_df.to_sql("silver_members", con, index=False)
    products.to_sql("silver_products", con, index=False)
    sessions_df.to_sql("silver_web_sessions", con, index=False)
    ads_df.to_sql("silver_ad_spend", con, index=False)
    promos_df.to_sql("silver_promotions", con, index=False)
    camps_df.to_sql("silver_campaigns", con, index=False)

    con.executescript("""
    -- ===== GOLD: 주문·품목 표준 뷰
    CREATE VIEW gold_order_lines AS
    SELECT date(o.order_ts) AS order_date, o.order_id, o.member_id, o.channel, o.campaign_id, o.coupon_code,
           i.product_id, p.product_name, p.category, i.qty, i.item_amount AS gross_amount,
           i.discount_amount, i.item_amount - i.discount_amount AS net_amount,
           p.cost_price * i.qty AS cost_amount, i.returned_qty,
           CASE WHEN i.returned_qty > 0 THEN i.item_amount - i.discount_amount ELSE 0 END AS return_amount,
           CASE WHEN date(o.order_ts) = f.first_order_date THEN 1 ELSE 0 END AS is_first_order
    FROM silver_orders o
    JOIN silver_order_items i ON o.order_id = i.order_id
    JOIN silver_products p ON i.product_id = p.product_id
    JOIN (SELECT member_id, MIN(date(order_ts)) AS first_order_date FROM silver_orders GROUP BY member_id) f
      ON o.member_id = f.member_id;

    CREATE VIEW gold_customers AS
    SELECT m.member_id, m.joined_at, m.grade, m.gender, m.age_band, m.signup_channel,
           MIN(date(o.order_ts)) AS first_order_date, MAX(date(o.order_ts)) AS last_order_date,
           COUNT(DISTINCT o.order_id) AS order_count,
           SUM(o.total_amount - o.discount_amount) AS total_net_amount
    FROM silver_members m LEFT JOIN silver_orders o ON m.member_id = o.member_id
    GROUP BY m.member_id;

    -- ===== G1: 디멘션
    CREATE TABLE g1_dim_product AS SELECT product_id, product_name, category, brand, list_price, cost_price, launched_at FROM silver_products;
    CREATE TABLE g1_dim_campaign AS SELECT * FROM silver_campaigns;

    -- ===== G1: 팩트
    CREATE TABLE g1_fact_sales_daily AS
    SELECT order_date AS date, channel, campaign_id, category, product_id,
           COUNT(DISTINCT order_id) AS orders, SUM(qty) AS units,
           SUM(gross_amount) AS gross_revenue, SUM(discount_amount) AS discount_amount,
           SUM(net_amount) AS net_revenue, SUM(net_amount - cost_amount) AS gross_profit,
           SUM(returned_qty) AS return_units, SUM(return_amount) AS return_amount,
           COUNT(DISTINCT CASE WHEN is_first_order = 1 THEN order_id END) AS new_customer_orders
    FROM gold_order_lines GROUP BY 1,2,3,4,5;

    CREATE TABLE g1_fact_marketing_daily AS
    SELECT a.date, a.channel, a.campaign_id, a.impressions, a.clicks, a.spend,
           COALESCE(s.sessions, 0) AS sessions,
           COALESCE(o.orders, 0) AS conversions, COALESCE(o.revenue, 0) AS attributed_revenue
    FROM silver_ad_spend a
    LEFT JOIN silver_web_sessions s ON s.session_date = a.date AND s.campaign_id = a.campaign_id
    LEFT JOIN (SELECT date(order_ts) d, campaign_id, COUNT(*) orders, SUM(total_amount - discount_amount) revenue
               FROM silver_orders GROUP BY 1,2) o ON o.d = a.date AND o.campaign_id = a.campaign_id;

    CREATE TABLE g1_fact_customer_cohort AS
    WITH firsts AS (SELECT member_id, strftime('%Y-%m', MIN(order_ts)) AS cohort_month FROM silver_orders GROUP BY 1),
    acts AS (SELECT DISTINCT member_id, strftime('%Y-%m', order_ts) AS active_month FROM silver_orders)
    SELECT f.cohort_month, a.active_month,
           (CAST(substr(a.active_month,1,4) AS INT)*12 + CAST(substr(a.active_month,6,2) AS INT))
         - (CAST(substr(f.cohort_month,1,4) AS INT)*12 + CAST(substr(f.cohort_month,6,2) AS INT)) AS months_since_first,
           COUNT(*) AS active_customers
    FROM firsts f JOIN acts a ON f.member_id = a.member_id GROUP BY 1,2;

    -- ===== 도메인
    CREATE TABLE domain_promotion_calendar AS SELECT * FROM silver_promotions;
    """)
    # 계절성 트렌드 (외부 트렌드 지수 흉내)
    weeks = pd.date_range(START, END, freq="W-MON")
    trend = []
    for w in weeks:
        m = w.month
        for cat in CATEGORIES:
            base = {"아우터": 40 + (m - 6) * 15, "상의": 80 - (m - 6) * 6, "하의": 60, "잡화": 50, "신발": 55}[cat]
            trend.append(dict(week_start=str(w.date()), category=cat, trend_index=round(base * rng.uniform(0.9, 1.1), 1)))
    pd.DataFrame(trend).to_sql("domain_season_trend", con, index=False)
    con.commit()
    con.close()
    print(f"OK: {out}  orders={len(orders_df):,} items={len(items_df):,} members={len(members_df):,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "warehouse.db"))
    main(Path(ap.parse_args().out))
