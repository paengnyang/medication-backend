
import os
import time
import xml.etree.ElementTree as ET

import requests
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

API_URL = (
    "https://apis.data.go.kr/1471000/"
    "MdcinPrdctnImportAcmsltService02/"
    "getMdcinPrdctnImportrstList02"
)

# 동일 품목의 API 반복 호출 감소
CACHE_SECONDS = 24 * 60 * 60
_cache = {}


def _key():
    return (
        os.getenv("MFDS_PRODUCTION_API_KEY")
        or os.getenv("MFDS_SERVICE_KEY")
        or os.getenv("MFDS_API_KEY")
        or os.getenv("DATA_GO_KR_SERVICE_KEY")
        or ""
    ).strip()


def _normalize(value):
    return "".join(
        str(value or "").lower().split()
    )


def _unwrap(data):
    """식약처 JSON/XML 응답을 리스트로 변환."""
    if isinstance(data, dict):
        if "ITEM_SEQ" in data or "itemSeq" in data:
            return [data]

        result = []

        for value in data.values():
            result.extend(_unwrap(value))

        return result

    if isinstance(data, list):
        result = []

        for value in data:
            result.extend(_unwrap(value))

        return result

    return []


def _parse_response(response):
    content = response.text.lstrip()

    if content.startswith("{") or content.startswith("["):
        return _unwrap(response.json())

    root = ET.fromstring(response.content)

    result = []

    for element in root.iter():
        if element.tag.split("}")[-1].lower() != "item":
            continue

        row = {}

        for child in element:
            name = child.tag.split("}")[-1]
            row[name] = child.text

        if row:
            result.append(row)

    return result


def _fetch_records(medicine_name):
    api_key = _key()

    if not api_key:
        raise RuntimeError(
            "MFDS_PRODUCTION_API_KEY가 설정되지 않았습니다."
        )

    
    # 검색 파라미터가 적용되는지 검증
    params = {
        "serviceKey": api_key,
        "pageNo": 1,
        "numOfRows": 100,
        "type": "json",
        "item_name": medicine_name,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=12,
    )
    # 실제 요청 주소에서 인증키는 가리고 출력
    safe_params = {
        key: ("***" if key.lower() == "servicekey" else value)
        for key, value in params.items()
        }

    response.raise_for_status()

    records = _parse_response(response)

    # 응답이 검색어를 실제 반영했는지 확인
    normalized_query = "".join(
        medicine_name.lower().split()
    )

    matching_records = [
        row for row in records
        if normalized_query in "".join(
            str(row.get("ITEM_NAME") or "")
            .lower()
            .split()
        )
    ]


    return matching_records



def _amount(value):
    try:
        number = float(
            str(value or "0").replace(",", "")
        )
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def get_market_performance(
    item_seq,
    medicine_name,
):
    """
    실제 식약처 생산/수입실적 조회.

    반환:
      status = found / not_found / error
      records = 최근 연도의 생산/수입 실적

    생산과 수입의 금액 단위는 다르므로
    별도 항목으로 보관한다.
    """
    item_seq = str(item_seq or "").strip()
    medicine_name = str(
        medicine_name or ""
    ).strip()

    if not item_seq or not medicine_name:
        return {
            "status": "not_found",
            "records": [],
        }

    now = time.time()
    cache_key = (item_seq, medicine_name)

    cached = _cache.get(cache_key)

    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    try:
        raw_records = _fetch_records(medicine_name)

        matched = []

        for row in raw_records:
            seq = str(
                row.get("ITEM_SEQ")
                or row.get("itemSeq")
                or ""
            ).strip()

            # 다른 제품의 실적 사용 금지
            if seq != item_seq:
                continue

            part = str(
                row.get("RESULT_PART")
                or ""
            ).strip()

            year = str(
                row.get("DATE_YEAR")
                or ""
            ).strip()

            amount = _amount(
                row.get("AMT")
            )

            if (
                part not in ("생산", "수입")
                or not year.isdigit()
                or amount is None
            ):
                continue

            matched.append({
                "year": int(year),
                "part": part,
                "amount": amount,
                "unit": (
                    "million_KRW"
                    if part == "생산"
                    else "USD"
                ),
                "itemSeq": seq,
            })

        if not matched:
            result = {
                "status": "not_found",
                "records": [],
            }
        else:
            # 가장 최근 연도의 데이터만 사용
            latest_year = max(
                row["year"] for row in matched
            )

            latest = [
                row for row in matched
                if row["year"] == latest_year
            ]

            # 동일 연도·구분별 합계
            grouped = {}

            for row in latest:
                part = row["part"]
                grouped[part] = (
                    grouped.get(part, 0)
                    + row["amount"]
                )

            result = {
                "status": "found",
                "latestYear": latest_year,
                "records": [
                    {
                        "year": latest_year,
                        "part": part,
                        "amount": amount,
                        "unit": (
                            "million_KRW"
                            if part == "생산"
                            else "USD"
                        ),
                    }
                    for part, amount in grouped.items()
                ],
            }

    except Exception as exc:
        print(
            "[MFDS PRODUCTION ERROR]",
            type(exc).__name__,
            str(exc),
        )
        # 식약처 장애가 기존 약 추천을 막지 않도록
        # 조회 실패를 명시적으로 반환
        result = {
            "status": "error",
            "records": [],
            "message": type(exc).__name__,
        }

    # 오류는 짧게, 정상 조회는 하루 동안 캐시
    ttl = 60 if result["status"] == "error" else CACHE_SECONDS
    _cache[cache_key] = (
        now - (CACHE_SECONDS - ttl),
        result,
    )

    return result
