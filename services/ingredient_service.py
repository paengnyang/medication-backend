
import os
import re
import sqlite3
from pathlib import Path

import requests


# 실제 연결에 성공한 식약처 API 주소 유지
MFDS_URL = (
    "https://apis.data.go.kr/1471000/"
    "DrugPrdtPrmsnInfoService08/"
    "getDrugPrdtPrmsnDtlInq08"
)

DB_PATH = Path(__file__).resolve().parent.parent / "ingredients.db"


# ========================================
# SQLite 연결
# ========================================
def connect_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingredients (
            name TEXT PRIMARY KEY
        )
    """)

    return conn


# ========================================
# 식약처 주성분명 정제
# ========================================
def extract_names(value):
    if not value:
        return []

    parts = re.split(r"[|,;\n]+", str(value))

    result = []

    for part in parts:
        name = part.strip()

        # [M040353] 등의 성분 코드 제거
        name = re.sub(
            r"^\[[^\]]+\]\s*",
            "",
            name,
        ).strip()

        if 2 <= len(name) <= 100:
            result.append(name)

    return result


# ========================================
# 식약처 API → SQLite 동기화
# ========================================
def sync_ingredients(max_pages=10):
    api_key = os.getenv("MFDS_SERVICE_KEY")

    if not api_key:
        raise RuntimeError(
            "MFDS_SERVICE_KEY가 설정되지 않았습니다."
        )

    total_saved = 0

    with connect_db() as conn:
        for page in range(1, max_pages + 1):
            try:
                response = requests.get(
                    MFDS_URL,
                    params={
                        "serviceKey": api_key,
                        "type": "json",
                        "pageNo": page,
                        "numOfRows": 100,
                    },
                    timeout=20,
                )
            except requests.RequestException:
                raise RuntimeError(
                    f"식약처 API 연결 실패 (페이지 {page})"
                ) from None

            if response.status_code != 200:
                raise RuntimeError(
                    f"식약처 API 오류: HTTP "
                    f"{response.status_code} (페이지 {page})"
                )

            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(
                    f"식약처 API JSON 응답 오류 (페이지 {page})"
                ) from None

            root = data.get("response", data)
            header = root.get("header", {})

            if str(header.get("resultCode", "00")) != "00":
                raise RuntimeError(
                    f"식약처 API 응답 오류 (페이지 {page}): "
                    f"{header.get('resultCode')}"
                )

            body = root.get("body", {})
            items = body.get("items", [])

            if isinstance(items, dict):
                items = items.get("item", [])

            if isinstance(items, dict):
                items = [items]

            if not items:
                print(f"페이지 {page}: 데이터 없음")
                break

            if not isinstance(items, list):
                raise RuntimeError(
                    f"페이지 {page}: 예상하지 못한 데이터 형식"
                )

            page_saved = 0

            for item in items:
                if not isinstance(item, dict):
                    continue

                ingredient_text = item.get(
                    "MAIN_ITEM_INGR"
                )

                names = extract_names(ingredient_text)

                for name in names:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO ingredients(name)
                        VALUES (?)
                        """,
                        (name,),
                    )

                    page_saved += cursor.rowcount
                    total_saved += cursor.rowcount

            print(
                f"페이지 {page}: "
                f"{len(items)}건 조회, "
                f"신규 성분 {page_saved}개"
            )

        # with 구문 종료 시 정상적으로 commit

    with connect_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM ingredients"
        ).fetchone()[0]

    print(f"신규 저장 성분: {total_saved}개")
    print(f"전체 저장 성분: {total}개")

    return total_saved


# ========================================
# 성분명 자동완성 검색
# ========================================
def search_ingredients(query: str, limit: int = 3):
    keyword = query.strip()

    if len(keyword) < 2:
        return []

    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM ingredients
            WHERE instr(name, ?) > 0
            ORDER BY
                CASE
                    WHEN substr(name, 1, length(?)) = ? THEN 0
                    ELSE 1
                END,
                length(name),
                name
            LIMIT ?
            """,
            (
                keyword,
                keyword,
                keyword,
                limit,
            ),
        ).fetchall()

    return [row[0] for row in rows]
