import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import urllib.parse
import re

# ==========================================
# 1. 사용자 설정 정보
# ==========================================
NOTION_TOKEN = "ntn_n9230455858ahP4EMhkrguf0ld3JV7xXfM2hA9FQ1Ywbzj"
DATABASE_ID = "3ac2262d943280919d6ac501b9b9a8c7"
PUBLIC_DATA_KEY = "eee7f8f94d68563652f1330f65ec1ddb5e03a16c585a20159864fe8b1abc136f"

# ==========================================
# 2. 노션 API 설정
# ==========================================
notion_headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

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
            results = res.json().get("results", [])
            return len(results) > 0
    except Exception:
        pass
    return False

def add_to_notion(district, dong_name, apt_name, area, price, floor, deal_date, household_count=None):
    full_apt_name = f"[{district}] {apt_name}"
    
    # 중복 체크
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

    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": properties
    }
    try:
        res = requests.post(url, headers=notion_headers, json=data, timeout=10)
        if res.status_code == 200:
            h_text = f" | {household_count:,}세대" if household_count else ""
            print(f"✅ [노션 등록 성공] {full_apt_name} | {area}m² | {price:,}만원 | {floor}층{h_text} | {deal_date}")
        else:
            print(f"⚠️ 노션 응답 스킵 ({res.status_code}): {res.text[:100]}")
    except Exception as e:
        print(f"❌ 노션 연결 에러: {e}")

# ==========================================
# 3. 세대수 검색 로직
# ==========================================
def get_household_count(district, dong_name, apt_name):
    clean_apt = apt_name.split('(')[0].replace("아파트", "").strip()
    
    try:
        keyword = f"{district} {dong_name} {clean_apt}"
        encoded_keyword = urllib.parse.quote(keyword)
        search_url = f"https://m.land.naver.com/api/search/complexes?keyword={encoded_keyword}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
            'Referer': 'https://m.land.naver.com/'
        }
        res = requests.get(search_url, headers=headers, timeout=3)
        if res.status_code == 200:
            data = res.json()
            complexes = data.get('complexes', [])
            if complexes:
                for comp in complexes:
                    cnt = comp.get('totalHouseholdCount') or comp.get('totHshldCnt') or comp.get('hshldCnt')
                    if cnt and str(cnt).isdigit() and int(cnt) > 0:
                        return int(cnt)
    except Exception:
        pass

    return None

# ==========================================
# 4. 실거래가 데이터 수집 및 연동
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
                print(f"❌ 국토부 API 호출 실패 (상태코드: {response.status_code})")
                continue

            try:
                root = ET.fromstring(response.content)
            except Exception as e:
                print(f"❌ XML 파싱 에러: {e}")
                continue

            result_code = root.find('.//resultCode')
            if result_code is not None and result_code.text not in ['00', '000']:
                result_msg = root.find('.//resultMsg')
                msg = result_msg.text if result_msg is not None else "알 수 없는 에러"
                print(f"⚠️ API 응답 에러 (코드 {result_code.text}): {msg}")
                continue

            items = root.findall('.//item')
            print(f"📥 수집된 전체 거래 건수: {len(items)}건")

            count = 0
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

                    # 조건 필터링: 전용 84㎡ 이상 & 15억(150,000만원) 이하
                    if area >= 84.0 and price <= 150000:
                        household_cnt = get_household_count(dist_name, dong_name, apt_name)
                        add_to_notion(dist_name, dong_name, apt_name, round(area, 1), price, floor, deal_date, household_cnt)
                        count += 1

                except Exception:
                    continue

            print(f"👉 {dist_name} 조건 부합(30평대+ & 15억 이하) 신규 거래: {count}건 처리 완료")

        except Exception as e:
            print(f"❌ 네트워크 또는 처리 에러: {e}")

# 실행
fetch_and_sync_real_price()
