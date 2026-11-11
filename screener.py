import os
import json
import math
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 404 차단 방지를 위한 기본 User-Agent 글로벌 설정
opener = urllib.request.build_opener()
opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')]
urllib.request.install_opener(opener)

MIN_SCANNED = 500  # 이보다 적게 검사됐으면 가격 서버 장애로 보고 기존 결과를 덮어쓰지 않는다


def is_common_stock(code, name):
    """보통주만 남긴다. 우선주는 이름이 아니라 종목코드 끝자리로 거른다 (보통주는 0으로 끝남).
    이름에 '우'가 들어갔다고 빼면 우리금융지주·대우건설 같은 보통주까지 빠진다."""
    code = str(code)
    if not (code.isdigit() and len(code) == 6 and code.endswith('0')):
        return False
    return not any(k in str(name) for k in ('ETN', 'ETF', '스팩'))


def symbol_of(code, market):
    market = str(market)
    if market == 'KOSPI':
        return f"{code}.KS"
    if market.startswith('KOSDAQ'):   # KOSDAQ, KOSDAQ GLOBAL
        return f"{code}.KQ"
    return code


def check_trend(stock_df):
    """미너비니 트렌드 템플릿 7가지 조건. 통과하면 값 dict, 아니면 None."""
    if stock_df is None or len(stock_df) < 200:
        return None
    close = stock_df['Close']
    ma50 = close.rolling(window=50).mean().iloc[-1]
    ma150 = close.rolling(window=150).mean().iloc[-1]
    ma200_s = close.rolling(window=200).mean()
    ma200 = ma200_s.iloc[-1]
    ma200_1m_ago = ma200_s.iloc[-20] if len(stock_df) >= 220 else ma200_s.dropna().iloc[0]
    curr_price = close.iloc[-1]

    df_52w = stock_df.iloc[-250:] if len(stock_df) >= 250 else stock_df
    high_52w = df_52w['High'].max()
    low_52w = df_52w['Low'].min()

    # 가격 자료가 깨진 종목(저가 0, 빈 값 등)은 건너뛴다 → +inf% 방지
    values = [curr_price, ma50, ma150, ma200, ma200_1m_ago, high_52w, low_52w]
    if any((v is None) or (not math.isfinite(float(v))) or float(v) <= 0 for v in values):
        return None

    c1 = (curr_price > ma150) and (curr_price > ma200)
    c2 = ma150 > ma200
    c3 = ma200 > ma200_1m_ago
    c4 = (ma50 > ma150) and (ma50 > ma200)
    c5 = curr_price > ma50
    c6 = curr_price >= (low_52w * 1.25)
    c7 = curr_price >= (high_52w * 0.75)
    if not (c1 and c2 and c3 and c4 and c5 and c6 and c7):
        return None
    return {
        'price': int(curr_price),
        'pct_high': round((curr_price - high_52w) / high_52w * 100, 2),
        'pct_low': round((curr_price - low_52w) / low_52w * 100, 2),
    }


def run_screener():
    # 1. 한국 표준시(KST) 기준 현재 시간 구하기
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{now_str} KST] 1. KRX 전체 종목 목록 수집 중...")
    
    try:
        df_krx = fdr.StockListing('KRX')
    except Exception as e:
        print(f"KRX 목록 수집 실패 (기본 KOSPI/KOSDAQ으로 재시도): {e}")
        try:
            df_kospi = fdr.StockListing('KOSPI')
            df_kosdaq = fdr.StockListing('KOSDAQ')
            df_krx = pd.concat([df_kospi, df_kosdaq], ignore_index=True)
        except Exception as ex:
            print(f"종목 목록 수집 불가: {ex}")
            return

    df = df_krx[['Market', 'Code', 'Name']].copy()
    df['Market'] = df['Market'].fillna('KONEX')

    # 보통주만 (우선주·ETF·ETN·스팩 제외)
    df_filtered = df[[is_common_stock(c, n) for c, n in zip(df['Code'], df['Name'])]].copy()

    results = []
    scanned = 0
    print(f"2. 총 {len(df_filtered)}개 종목 미너비니 트렌드 템플릿(7가지 조건) 스크리닝 진행...")

    # DataReader용 날짜 설정
    end_date = datetime.now()
    start_date = end_date - timedelta(days=500)

    for _, row in df_filtered.iterrows():
        symbol_code = str(row['Code']).zfill(6)
        market = row['Market']
        name = row['Name']
        
        full_symbol = symbol_of(symbol_code, market)

        try:
            # 개별 종목 데이터 수집 (HTTP 404 발생 시 스킵)
            stock_df = fdr.DataReader(symbol_code, start_date, end_date)
            if stock_df is None or len(stock_df) < 200:
                continue
            scanned += 1
            hit = check_trend(stock_df)
            if hit:
                clean_name = str(name).replace("\\", "\\\\").replace("'", "\\'")
                results.append({'symbol': full_symbol, 'code': symbol_code, 'name': clean_name, 'raw_name': str(name), 'market': market, **hit})
        except Exception:
            # 단일 종목 404 및 수집 실패 시 전체 중단 없이 다음 종목으로 통과
            continue

    print(f"3. 검사 완료: {scanned}개 중 {len(results)}개 통과")
    if scanned < MIN_SCANNED:
        print(f"검사된 종목이 {scanned}개뿐입니다 (가격 서버 장애로 판단). 기존 결과를 그대로 둡니다.")
        return

    result_df = pd.DataFrame(results)
    
    # 웹 배포용 폴더 생성
    os.makedirs("public", exist_ok=True)

    if not result_df.empty:
        result_df = result_df.sort_values(by='pct_high', ascending=False).reset_index(drop=True)

    # 0) screener.json — 다른 사이트(yplue.com)가 읽어 가는 결과 파일. 통과 종목이 0개여도 쓴다.
    with open('public/screener.json', 'w', encoding='utf-8') as f:
        json.dump({
            'version': 2,
            'updatedAt': now_str,          # 한국 시각
            'scanned': scanned,            # 가격을 받아 검사한 종목 수
            'count': len(result_df),
            'items': [
                {'code': r['code'], 'symbol': r['symbol'], 'name': r['raw_name'], 'market': r['market'],
                 'price': int(r['price']), 'pctHigh': float(r['pct_high']), 'pctLow': float(r['pct_low'])}
                for _, r in result_df.iterrows()
            ],
        }, f, ensure_ascii=False, indent=1)

    if not result_df.empty:

        # 1) KR_DB.js 내보내기
        with open('public/KR_DB.js', 'w', encoding='utf-8') as f:
            f.write(f"// 한국 표준시(KST) 갱신 시각: {now_str}\n")
            f.write("const KR_DB = [\n")
            for _, row in result_df.iterrows():
                f.write(f"  {{ symbol: '{row['symbol']}', name: '{row['name']}', market: '{row['market']}' }},\n")
            f.write("];\nexport default KR_DB;\n")

        # 2) HTML 웹페이지 생성 (index.html)
        html_rows = ""
        for _, row in result_df.iterrows():
            badge_class = "badge-kospi" if row['market'] == 'KOSPI' else "badge-kosdaq"
            html_rows += f"""
            <tr>
                <td><strong>{row['symbol']}</strong></td>
                <td><strong>{row['name']}</strong></td>
                <td><span class="badge {badge_class}">{row['market']}</span></td>
                <td style="text-align: right;">{row['price']:,} 원</td>
                <td style="text-align: right; color: #c92a2a;">{row['pct_high']}%</td>
                <td style="text-align: right; color: #2b8a3e;">+{row['pct_low']}%</td>
            </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>미너비니 주식 스크리너</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 20px; background-color: #f8f9fa; margin:0; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: #fff; padding: 24px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
        .header {{ background-color: #1a2530; color: #fff; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .header h2 {{ margin: 0 0 8px 0; }}
        .header p {{ margin: 0; color: #adb5bd; font-size: 13px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th {{ background-color: #212529; color: white; padding: 12px; text-align: left; font-size: 13px; }}
        td {{ padding: 10px 12px; border-bottom: 1px solid #dee2e6; font-size: 14px; }}
        tr:hover {{ background-color: #f8f9fa; }}
        .badge {{ padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; color: white; }}
        .badge-kospi {{ background-color: #0052cc; }}
        .badge-kosdaq {{ background-color: #28a745; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>📈 마크 미너비니 트렌드 스크리닝 결과</h2>
            <p>최종 갱신 시각: {now_str} (KST) | 포착 종목 수: {len(result_df)}개</p>
            <span class="warning">⚠️ 모든 투자의 책임은 본인에게 있습니다. 정보는 참조만 하세요</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>종목명</th>
                    <th>시장</th>
                    <th style="text-align: right;">현재가</th>
                    <th style="text-align: right;">52주 고점대비</th>
                    <th style="text-align: right;">52주 저점대비</th>
                </tr>
            </thead>
            <tbody>
                {html_rows}
            </tbody>
        </table>
    </div>
</body>
</html>"""

        with open("public/index.html", "w", encoding="utf-8") as f:
            f.write(html_content)

if __name__ == "__main__":
    run_screener()
