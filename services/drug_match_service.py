
import re
import time
from rapidfuzz import process, fuzz

from services.drug_api_service import search_drug_by_name


# 식약처 검색 결과 메모리 캐시: 1시간
SEARCH_CACHE_SECONDS = 3600
_search_cache = {}


def normalize_medicine_name(name: str) -> str:
    if not name:
        return ""

    name = str(name).strip()
    name = name.replace("캡슬", "캡슐")

    # 괄호 속 성분명 등은 이름 비교에서 제외
    name = re.split(r"[\(\[\{]", name, maxsplit=1)[0]

    # 앞쪽 기호 제거
    name = re.sub(r"^[\*\-·•]+", "", name)

    # 단위 표기 통일
    name = re.sub(
        r"밀리그램",
        "mg",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"밀리리터",
        "ml",
        name,
        flags=re.IGNORECASE,
    )

    # 공백과 일부 구분기호 제거
    name = re.sub(r"[\s·\-_./]+", "", name)

    return name.lower()


def get_medicine_form(name: str):
    forms = [
        "연질캡슐", "캡슐", "현탁액",
        "점안액", "흡입제", "패취",
        "패치", "연고", "크림",
        "시럽", "과립", "겔",
        "주사", "정",
    ]

    normalized = normalize_medicine_name(name)

    for form in forms:
        if normalized.endswith(form):
            return form

    return None


def _search_mfds(query: str):
    """같은 검색어의 식약처 호출을 1시간 재사용"""
    query = str(query or "").strip()

    if not query:
        return []

    key = normalize_medicine_name(query)
    cached = _search_cache.get(key)

    if cached:
        expires_at, medicines = cached

        if time.monotonic() < expires_at:
            print("[DRUG MATCH] 검색 캐시 사용:", query)
            return medicines

        _search_cache.pop(key, None)

    try:
        medicines = search_drug_by_name(query)

        if not isinstance(medicines, list):
            medicines = []

        _search_cache[key] = (
            time.monotonic() + SEARCH_CACHE_SECONDS,
            medicines,
        )

        return medicines

    except Exception as e:
        print(
            "[DRUG MATCH] 식약처 검색 실패:",
            query,
            str(e),
        )
        # 호출 실패는 빈 결과로 캐시하지 않음
        return []


def _build_candidates(ocr_name: str):
    """원본 검색 후 필요한 경우 제형 뒤 OCR 숫자 제거 검색"""
    original = str(ocr_name or "").strip()

    if not original:
        return []

    queries = [original]

    # 예: 파모터정20079 -> 파모터정
    # 단, 함량이 사라지므로 결과는 자동 확정하지 않음
    fallback = re.sub(
        r"(정|캡슐|시럽|연질캡슐)\d{2,}$",
        r"\1",
        re.sub(r"\s+", "", original),
    )

    if (
        fallback != original
        and fallback not in queries
    ):
        queries.append(fallback)

    results = []
    seen = set()

    for query in queries[:2]:
        medicines = _search_mfds(query)

        for drug in medicines:
            if not isinstance(drug, dict):
                continue

            item_name = str(
                drug.get("itemName") or ""
            ).strip()

            item_seq = str(
                drug.get("itemSeq") or ""
            ).strip()

            if not item_name or not item_seq:
                continue

            if item_seq in seen:
                continue

            seen.add(item_seq)
            results.append(drug)

    return results


def match_medicine_name(ocr_name: str):
    original = str(ocr_name or "").strip()
    normalized = normalize_medicine_name(original)

    def result(
        matched_name=None,
        score=0.0,
        auto=False,
        valid=False,
        item_seq=None,
    ):
        return {
            "originalName": original,
            "matchedName": matched_name or original,
            "suggestedName": matched_name,
            "score": round(float(score), 1),
            "autoCorrected": auto,
            "needsReview": not auto,
            "isValidMedicine": valid,
            # 자동 확정된 약에만 품목기준코드 부여
            "itemSeq": item_seq if auto else None,
        }

    # 복용 수량, 단순 제형 설명 등은 약 후보가 아님
    if (
        not normalized
        or re.fullmatch(
            r"\d+(?:\.\d+)?(?:정|캡슐|시럽)",
            normalized,
        )
    ):
        return result()

    candidates = _build_candidates(original)

    if not candidates:
        # OCR 후보는 파서에서 유지하고 사용자 검토
        return result()

    normalized_map = {}

    for drug in candidates:
        item_name = str(
            drug.get("itemName") or ""
        ).strip()

        key = normalize_medicine_name(item_name)

        if key:
            normalized_map.setdefault(
                key, []
            ).append(drug)

    if not normalized_map:
        return result()

    # 품목명까지 정확히 일치하는지 우선 확인
    exact_matches = normalized_map.get(
        normalized, []
    )

    if len(exact_matches) == 1:
        drug = exact_matches[0]

        print(
            "[DRUG MATCH] 정확히 일치:",
            original,
            "->",
            drug["itemName"],
        )

        return result(
            matched_name=drug["itemName"],
            score=100.0,
            auto=True,
            valid=True,
            item_seq=str(drug["itemSeq"]),
        )

    # 이름은 같더라도 여러 품목코드가 있다면 직접 확인
    if len(exact_matches) > 1:
        return result(
            matched_name=exact_matches[0]["itemName"],
            score=100.0,
            auto=False,
            valid=True,
        )

    # 유사한 이름을 제안만 하고 자동 확정하지 않음
    best = process.extractOne(
        normalized,
        list(normalized_map.keys()),
        scorer=fuzz.WRatio,
    )

    if best is None:
        return result()

    matched_key, score, _ = best
    drug = normalized_map[matched_key][0]

    print(
        "[DRUG MATCH] 후보:",
        original,
        "->",
        drug["itemName"],
        "score=",
        round(score, 1),
    )

    return result(
        matched_name=drug["itemName"],
        score=score,
        auto=False,
        valid=(score >= 65),
    )
