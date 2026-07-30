import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import urllib.parse
import os

# ==========================================
# 1. 사용자 설정 정보 및 보안 키 환경변수 로드
# ==========================================
NOTION_TOKEN = "ntn_n9230455858ahP4EMhkrguf0ld3JV7xXfM2hA9FQ1Ywbzj"
DATA_DATABASE_ID = "3ac2262d943280919d6ac501b9b9a8c7"    # 아파트 실거래가 수집용 DB
REPORT_DATABASE_ID = "3ad2262d943280cba18ed23ab11440c5"  # 주간 마케팅 보고서 전용 DB

PUBLIC_DATA_KEY = "eee7f8f94d68563652f1330f65ec1ddb5e03a16c585a20159864fe8b1abc136f"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

collected_deals = []

# ==========================================
# 2. 노션 데이터 중복 체크 및 개별 거래 등록 (실거래가 DB)
# ==========================================
def check_duplicate(apt_name, deal_date, price, floor):
    url = f"https://api.notion.com/v1/databases/{DATA_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "아파트명", "title": {"equals": apt_name}},
                {"property": "계약일자", "date": {"equals": deal_date}},
                {"property": "거래금액(만원)", "number": {"equals": price}},
                {"property": "층수", "number": {"equals": floor}}
            ]
        }
    }
    try:
        res = requests.post(url, headers=notion_headers, json=payload, timeout=10)
        if res.status_code == 200:
            return len(res.json().get("results", [])) > 0
    except Exception:
        pass
    return False

def add_to_notion(district, dong_name, apt_name, area, price, floor, deal_date, household_count=None):
    full_apt_name = f"[{district}] {apt_name}"
    
    collected_deals.append({
        "district": district, "dong": dong_name, "apt": apt_name,
        "price": price, "area": area, "household": household_count or 0, "date": deal_date
    })

    if check_duplicate(full_apt_name, deal_date, price, floor):
        print(f"⏩ [중복 스킵] {full_apt_name} ({price:,}만원)")
        return

    url = "https://api.notion.com/v1/pages"
    properties = {
        "아파트명": {"title": [{"text": {"content": full_apt_name}}]},
        "평형": {"rich_text": [{"text": {"content": f"{area}m² (30평대+)"}}]},
        "거래금액(만원)": {"number": price},
        "층수": {"number": floor},
        "계약일자": {"date": {"start": deal_date}}
    }
    if dong_name:
        properties["동이름"] = {"rich_text": [{"text": {"content": dong_name}}]}
    if household_count and household_count > 0:
        properties["세대수"] = {"number": household_count}

    try:
        res = requests.post(url, headers=notion_headers, json={"parent": {"database_id": DATA_DATABASE_ID}, "properties": properties}, timeout=10)
        if res.status_code == 200:
            print(f"✅ [실거래 노션 등록] {full_apt_name} | {price:,}만원 | {deal_date}")
    except Exception as e:
        print(f"❌ 실거래 노션 등록 에러: {e}")

# ==========================================
# 3. 네이버 부동산 세대수 수집
# ==========================================
def get_household_count(district, dong_name, apt_name):
    clean_apt = apt_name.split('(')[0].replace("아파트", "").strip()
    try:
        keyword = f"{district} {dong_name} {clean_apt}"
        url = f"https://m.land.naver.com/api/search/complexes?keyword={urllib.parse.quote(keyword)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.land.naver.com/'}
        res = requests.get(url, headers=headers, timeout=3)
        if res.status_code == 200:
            for comp in res.json().get('complexes', []):
                cnt = comp.get('totalHouseholdCount') or comp.get('totHshldCnt') or comp.get('hshldCnt')
                if cnt and str(cnt).isdigit() and int(cnt) > 0:
                    return int(cnt)
    except Exception:
        pass
    return None

# ==========================================
# 4. Gemini AI 구별/동별 시세 분석 주간 보고서 생성
# ==========================================
def generate_ai_report(deals):
    if not deals:
        print("ℹ️ 수집된 실거래 데이터가 없어 보고서를 생성하지 않습니다.")
        return None

    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY가 설정되지 않아 AI 보고서 생성을 스킵합니다.")
        return None

    total_count = len(deals)
    songpa_deals = [d for d in deals if d['district'] == '송파구']
    gangdong_deals = [d for d in deals if d['district'] == '강동구']

    def get_stats(d_list):
        if not d_list:
            return "거래 없음"
        avg_p = sum(d['price'] for d in d_list) // len(d_list)
        max_d = max(d_list, key=lambda x: x['price'])
        min_d = min(d_list, key=lambda x: x['price'])
        return f"거래 {len(d_list)}건 | 평균가: {avg_p:,}만원 | 최고가: {max_d['apt']}({max_d['price']:,}만원) | 최저가: {min_d['apt']}({min_d['price']:,}만원)"

    dong_summary = {}
    for d in deals:
        key = f"{d['district']} {d['dong']}"
        dong_summary.setdefault(key, []).append(d['price'])

    dong_text_list = []
    for dong, prices in dong_summary.items():
        avg_p = sum(prices) // len(prices)
        dong_text_list.append(f"- {dong}: 총 {len(prices)}건 거래 | 평균가 {avg_p:,}만원 (최고 {max(prices):,}만원 / 최저 {min(prices):,}만원)")

    data_summary = f"""
    [전체 요약]
    - 총 수집 거래건수: {total_count}건

    [구별 시세 동향]
    - 송파구 현황: {get_stats(songpa_deals)}
    - 강동구 현황: {get_stats(gangdong_deals)}

    [동별 상세 시세 현황]
    """ + "\n".join(dong_text_list[:15]) + f"""

    [주요 단지 상세 거래 샘플]
    """ + "\n".join([f"  * [{d['district']} {d['dong']}] {d['apt']} ({d['area']}m²) | {d['price']:,}만원 | {d['household']}세대 | 계약일: {d['date']}" for d in deals[:20]])

    prompt = f"""
    당신은 통신사 마케팅 전략 수립을 담당하는 부동산 데이터 분석 전문가입니다.
    아래 수집된 송파구 및 강동구 아파트 실거래가 데이터를 바탕으로, 통신 마케팅 팀에 공유할 '주간 부동산 시세 동향 & 타깃 마케팅 브리핑 보고서'를 작성해 주세요.

    [수집 데이터 정보]
    {data_summary}

    [보고서 필수 작성 목차]
    1. 📊 주간 핵심 시장 요약 (3줄 핵심 요약)
    2. 🏙️ 구별 · 동별 시세 및 거래 흐름 분석
       - 송파구 vs 강동구 구별 시세격차 및 특징 비교
       - 거래가 활발한 동별(예: 가락동, 고덕동 등) 평균 시세 수준 및 주도 단지 분석
       - 대단지(세대수 높은 아파트) 위주의 거래 가격대 형성 분석
    3. 💡 통신사 마케팅 실행 전략 (BTL 및 결합상품 영업)
       - 이사 수요가 많아 인터넷+TV 결합 상품 집중 판촉이 필요한 추천 타깃 동 및 단지
       - 인근 매장/영업 인력 투입을 위한 현장 프로모션 가이드

    보고서 작성 시 제목과 강조 표시를 활용하여 읽기 쉽고 명확한 전문적인 보고서 톤으로 작성해 주세요.
    """

    try:
        # Gemini 최신 모델 API 엔드포인트 경로로 수정 완료
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"❌ Gemini API 오류: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ AI 보고서 생성 에러: {e}")
    return None

# ==========================================
# 5. 전용 보고서 DB에 주간 보고서 페이지 발행
# ==========================================
def publish_report_to_notion(report_text):
    if not report_text:
        return

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    title = f"📈 [주간 시세 리포트] 송파/강동 구별·동별 시세 분석 & 마케팅 전략 ({today_str})"

    url = "https://api.notion.com/v1/pages"
    
    blocks = []
    lines = report_text.split("\n")
    for line in lines:
        if line.strip():
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line}}]
                }
            })

    data = {
        "parent": {"database_id": REPORT_DATABASE_ID},
        "properties": {
            "이름": {"title": [{"text": {"content": title}}]}
        },
        "children": blocks[:90]
    }

    try:
        res = requests.post(url, headers=notion_headers, json=data, timeout=15)
        if res.status_code == 200:
            print(f"\n🎉 [주간 보고서 발행 성공] '{title}' 보고서 전용 DB 등록 완료!")
        else:
            print(f"❌ 보고서 노션 발행 실패: {res.text[:100]}")
    except Exception as e:
        print(f"❌ 보고서 노션 발행 에러: {e}")

# ==========================================
# 6. 메인 실행 프로세스
# ==========================================
def fetch_and_sync_real_price():
    districts = {"송파구": "11710", "강동구": "11740"}
    target_ym = datetime.now().strftime("%Y%m")
    base_url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

    for dist_name, code in districts.items():
        print(f"\n🔍 {dist_name} 데이터 조회 중 ({target_ym})...")
        request_url = f"{base_url}?serviceKey={PUBLIC_DATA_KEY}&LAWD_CD={code}&DEAL_YMD={target_ym}&numOfRows=500&pageNo=1"

        try:
            response = requests.get(request_url, timeout=20)
            if response.status_code != 200:
                continue
            root = ET.fromstring(response.content)

            items = root.findall('.//item')
            for item in items:
                try:
                    apt_element = item.find('aptNm') if item.find('aptNm') is not None else item.find('아파트')
                    area_element = item.find('excluUseAr') if item.find('excluUseAr') is not None else item.find('전용면적')
                    price_element = item.find('dealAmount') if item.find('dealAmount') is not None else item.find('거래금액')
                    floor_element = item.find('floor') if item.find('floor') is not None else item.find('층')
                    dong_element = item.find('umdNm') if item.find('umdNm') is not None else item.find('법정동')

                    if None in [apt_element, area_element, price_element, floor_element]:
                        continue

                    apt_name = apt_element.text.strip() if apt_element.text else ""
                    dong_name = dong_element.text.strip() if (dong_element is not None and dong_element.text) else ""
                    area = float(area_element.text.strip())
                    price = int(price_element.text.strip().replace(',', ''))
                    floor = int(floor_element.text.strip())

                    year = item.find('dealYear').text.strip() if item.find('dealYear') is not None else item.find('년').text.strip()
                    month = item.find('dealMonth').text.strip().zfill(2) if item.find('dealMonth') is not None else item.find('월').text.strip().zfill(2)
                    day = item.find('dealDay').text.strip().zfill(2) if item.find('dealDay') is not None else item.find('일').text.strip().zfill(2)
                    deal_date = f"{year}-{month}-{day}"

                    if area >= 84.0 and price <= 150000:
                        household_cnt = get_household_count(dist_name, dong_name, apt_name)
                        add_to_notion(dist_name, dong_name, apt_name, round(area, 1), price, floor, deal_date, household_cnt)

                except Exception:
                    continue
        except Exception as e:
            print(f"❌ 데이터 수집 에러: {e}")

    print("\n🤖 Gemini AI 구별·동별 주간 마케팅 보고서 생성 중...")
    report = generate_ai_report(collected_deals)
    if report:
        publish_report_to_notion(report)

# 실행
fetch_and_sync_real_price()
