
import re
from rapidfuzz import fuzz

from services.drug_api_service import search_drug_by_name


def _normalize_name(name):
    """의약품명 비교용 정규화"""
    name = str(name or "")

    # 성분 설명은 제품명 비교에서 제외
    name = name.split("(")[0]

    name = name.replace("캡슬", "캡슐")
    name = name.replace("밀리그램", "mg")

    return re.sub(
        r"[\s·._\-]+",
        "",
        name,
    ).lower()


def _get_search_keywords(ocr_name):
    """
    잘못 읽힌 의약품명에서 부분 검색어 생성.

    예:
    엔시드6시간이일서방정 -> 엔시드
    가스모펀정 -> 가스모
    """
    name = _normalize_name(ocr_name)

    keywords = []

    # 숫자 앞에 있는 제품명 부분 우선
    before_number = re.match(
        r"^([가-힣]{2,})(?=\d)",
        name,
    )

    if before_number:
        keywords.append(
            before_number.group(1)
        )

    # 숫자가 없는 일반 제품명은
    # 앞쪽 3글자를 부분 검색에 사용
    leading_korean = re.match(
        r"^([가-힣]{3,})",
        name,
    )

    if leading_korean:
        prefix = leading_korean.group(1)
        keywords.append(prefix[:3])

    # 지나치게 짧거나 중복된 검색어 제외
    return list(dict.fromkeys(
        keyword
        for keyword in keywords
        if len(keyword) >= 3
    ))[:2]


def search_medicine_candidates(
    ocr_name,
    limit=5,
):
    """
    OCR 의약품명 검색 실패 시
    식약처 부분 검색으로 공식 의약품 후보 제공.

    절대 자동으로 의약품을 확정하지 않는다.
    """
    original = str(ocr_name or "").strip()

    if not original:
        return []

    keywords = _get_search_keywords(original)

    if not keywords:
        return []

    ocr_normalized = _normalize_name(original)

    ocr_numbers = re.findall(
        r"\d+(?:\.\d+)?",
        ocr_normalized,
    )

    found = {}

    for keyword in keywords:
        try:
            drugs = search_drug_by_name(keyword)

            print(
                "[DRUG CANDIDATE] 부분 검색:",
                keyword,
                "결과:",
                len(drugs),
            )

        except Exception as e:
            print(
                "[DRUG CANDIDATE] 검색 실패:",
                keyword,
                str(e),
            )
            continue

        for drug in drugs:
            item_name = str(
                drug.get("itemName") or ""
            ).strip()

            item_seq = str(
                drug.get("itemSeq") or ""
            ).strip()

            if not item_name or not item_seq:
                continue

            official = _normalize_name(item_name)

            # 검색한 제품명 부분이 실제 공식 이름에
            # 포함된 제품만 후보에 사용
            if keyword not in official:
                continue

            official_numbers = re.findall(
                r"\d+(?:\.\d+)?",
                official,
            )

            number_conflict = (
                bool(ocr_numbers)
                and ocr_numbers != official_numbers
            )

            score = round(
                fuzz.WRatio(
                    ocr_normalized,
                    official,
                ),
                1,
            )

            candidate = {
                "itemName": item_name,
                "itemSeq": item_seq,
                "entpName": str(
                    drug.get("entpName") or ""
                ),
                "mainIngredient": str(
                    drug.get("mainIngredient") or ""
                ),
                "matchScore": score,
                "numberConflict": number_conflict,
                "requiresConfirmation": True,
            }

            previous = found.get(item_seq)

            if (
                previous is None
                or score > previous["matchScore"]
            ):
                found[item_seq] = candidate

    candidates = sorted(
        found.values(),
        key=lambda item: (
            not item["numberConflict"],
            item["matchScore"],
        ),
        reverse=True,
    )

    return candidates[:limit]
