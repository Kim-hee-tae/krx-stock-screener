import os
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

    # 보통주 및 실제 주식 종목만 선별
    df_filtered = df[
        (~df['Name'].astype(str).str.contains('우|ETN|ETF|스팩', na=False)) & 
        (df['Code'].astype(str).str.isdigit())
    ].copy()

    results = []
    print(f"2. 총 {len(df_filtered)}개 종목 미너비니 기법 스크리닝 진행...")

    # DataReader용 날짜 설정
    end_date = datetime.now()
    start_date = end_date - timedelta(days=500)

    for _, row in df_filtered.iterrows():
        symbol_code = str(row['Code']).zfill(6)
        market = row['Market']
        name = row['Name']
        
        suffix = ".KS" if market == 'KOSPI' else (".KQ" if market == 'KOSDAK' else "")
        full_symbol = f"{symbol_code}{suffix}"
        
        try:
            # 개별 종목 데이터 수집 (HTTP 404 발생 시 스킵)
            stock_df = fdr.DataReader(symbol_code, start_date, end_date)
            if stock_df is None or len(stock_df) < 200:
                continue

            stock_df['MA50'] = stock_df['Close'].rolling(window=50).mean()
            stock_df['MA150'] = stock_df['Close'].rolling(window=150).mean()
            stock_df['MA200'] = stock_df['Close'].rolling(window=200).mean()

            curr_price = stock_df['Close'].iloc[-1]
            ma50 = stock_df['MA50'].iloc[-1]
            ma150 = stock_df['MA150'].iloc[-1]
            ma200 = stock_df['MA200'].iloc[-1]
            ma200_1m_ago = stock_df['MA200'].iloc[-20] if len(stock_df) >= 220 else stock_df['MA200'].iloc[0]

            df_52w = stock_df.iloc[-250:] if len(stock_df) >= 250 else stock_df
            high_52w = df_52w['High'].max()
            low_52w = df_52w['Low'].min()

            # 미너비니 8대 조건
            c1 = (curr_price > ma150) and (curr_price > ma200)
            c2 = ma150 > ma200
            c3 = ma200 > ma200_1m_ago
            c4 = (ma50 > ma150) and (ma50 > ma200)
            c5 = curr_price > ma50
            c6 = curr_price >= (low_52w * 1.25)
            c7 = curr_price >= (high_52w * 0.75)

            if c1 and c2 and c3 and c4 and c5 and c6 and c7:
                pct_from_high = ((curr_price - high_52w) / high_52w) * 100
                pct_from_low = ((curr_price - low_52w) / low_52w) * 100
                clean_name = str(name).replace("\\", "\\\\").replace("'", "\\'")
                
                results.append({
                    'symbol': full_symbol,
                    'code': symbol_code,
                    'name': clean_name,
                    'market': market,
                    'price': int(curr_price),
                    'pct_high': round(pct_from_high, 2),
                    'pct_low': round(pct_from_low, 2)
                })
        except Exception:
            # 단일 종목 404 및 수집 실패 시 전체 중단 없이 다음 종목으로 통과
            continue

    result_df = pd.DataFrame(results)
    
    # 웹 배포용 폴더 생성
    os.makedirs("public", exist_ok=True)

    if not result_df.empty:
        result_df = result_df.sort_values(by='pct_high', ascending=False).reset_index(drop=True)

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
            <b>모든 투자의 책임은 본인에게 있습니다. 정보는 참조만 하세요<b>
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
