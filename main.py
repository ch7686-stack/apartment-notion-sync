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
DATABASE_ID = "3ac2262d943280919d6ac501b9b9a8c7"
PUBLIC_DATA_KEY = "eee7f8f94d68563652f1330f65ec1ddb5e03a16c585a20159864fe8b1abc136f"

# GitHub Secrets에서 가져오는 Gemini API 키
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

collected_deals = []  # 보고서 생성을 위한 수집 데이터 보관함

# ==========================================
# 2. 노션 데이터 중복 체크 및 개별 거래 등록
# ==========================================
def check_duplicate(apt_name, deal_date, price, floor):
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
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
        res = requests.post(url, headers=notion_headers, json={"parent": {"database_id": DATABASE_ID}, "properties": properties}, timeout=10)
        if res.status_code == 200:
            print(f"✅ [노션 등록] {full_apt_name} | {price:,}만원 | {deal_date}")
            collected_deals.append({
                "district": district, "dong": dong_name, "apt": apt_name,
                "price": price, "area": area, "household": household_count or 0, "date": deal_date
            })
    except Exception as e:
        print(f"❌ 노션 등록 에러: {e}")

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
# 4. Gemini AI 주간 마케팅 보고서 생성
# ==========================================
def generate_ai_report(deals):
    if not deals:
        print("ℹ️ 이번 실행 시 신규 거래 데이터가 없어 주간 보고서를 생성하지 않습니다.")
        return None

    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY가 설정되지 않아 AI 보고서 생성을 스킵합니다.")
        return None

    total_count = len(deals)
    avg_price = sum(d['price'] for d in deals) // total_count
    max_deal = max(deals, key=lambda x: x['price'])
    
    data_summary = f"""
    - 주간 신규 거래 건수: 총 {total_count}건
    - 평균 거래가: {avg_price:,}만원
    - 최고가 거래: {max_deal['district']} {max_deal['apt']} ({max_deal['price']:,}만원, {max_deal['household']}세대)
    - 주요 거래 상세 목록:
    """ + "\n".join([f"  * [{d['district']} {d['dong']}] {d['apt']} | {d['price']:,}만원 | {d['household']}세대" for d in deals[:15]])

    prompt = f"""
    당신은 통신사 마케팅 전략 수립을 담당하는 부동산 데이터 분석 전문가입니다.
    아래 주간 아파트 실거래가 데이터를 바탕으로, 통신 마케팅 팀에 공유할 '주간 부동산 & 타깃 마케팅 브리핑 보고서'를 작성해 주세요.

    [수집 데이터 요약]
    {data_summary}

    [보고서 작성 목차]
    1. 📊 주간 핵심 시장 동향 요약 (3줄 요약)
    2. 🔥 주요 활발 거래 동/단지 분석 (세대수 및 가격 특징)
    3. 💡 통신사 마케팅 인사이트 & 추천 실행 전략
       - 이사/결합 상품(인터넷+TV) 집중 판촉 타깃 단지/지역 추천
       - 현장 BTL 프로모션 및 매장 영업 가이드

    가독성 좋게 단락 구분을 활용하여 전문적이고 명확한 톤으로 작성해 주세요.
    """

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=30)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"❌ Gemini API 오류 응답: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ AI 보고서 생성 중 예외 발생: {e}")
    return None

# ==========================================
# 5. 노션에 주간 보고서 페이지 발행
# ==========================================
def publish_report_to_notion(report_text):
    if not report_text:
        return

    today_str = datetime.now().strftime("%Y년 %m월 %d일")
    title = f"📈 [주간 브리핑] 송파/강동 부동산 실거래 & 마케팅 인사이트 ({today_str})"

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
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "아파트명": {"title": [{"text": {"content": title}}]}
        },
        "children": blocks[:90]
    }

    try:
        res = requests.post(url, headers=notion_headers, json=data, timeout=15)
        if res.status_code == 200:
            print(f"\n🎉 [주간 보고서 발행 성공] '{title}' 노션 추가 완료!")
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

                    # 조건: 전용 84㎡ 이상 & 15억 이하
                    if area >= 84.0 and price <= 150000:
                        household_cnt = get_household_count(dist_name, dong_name, apt_name)
                        add_to_notion(dist_name, dong_name, apt_name, round(area, 1), price, floor, deal_date, household_cnt)

                except Exception:
                    continue
        except Exception as e:
            print(f"❌ 데이터 수집 에러: {e}")

    # 모든 거래 수집 후 AI 주간 보고서 생성 및 노션 전송
    print("\n🤖 Gemini AI 주간 마케팅 보고서 생성 중...")
    report = generate_ai_report(collected_deals)
    if report:
        publish_report_to_notion(report)

# 실행
fetch_and_sync_real_price()
