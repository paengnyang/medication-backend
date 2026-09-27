import re
from rapidfuzz import process, fuzz


# ============================================================
# 임시 의약품 DB
#
# 현재는 OCR 보정 동작 확인용.
# 추후 식약처 의약품명 JSON/API 데이터로 교체 권장.
# ============================================================

MEDICINE_DATABASE = [
    "베포스타비정",
    "액티피드정",
    "라베프라정",
    "에르도스테인캡슐",
    "메드닌정",
    "크라모틴정",
    "펠루비정",
    "무코에스정",
]


# ============================================================
# 이름 정규화
# ============================================================

def normalize_medicine_name(name: str) -> str:
    """
    OCR 결과와 실제 의약품명을 비교하기 전에 정리한다.

    예:
    *배포스타바정(배포타스틴) -> 배포스타바정
    에르도스테인캡슬 -> 에르도스테인캡슐
    """
    if not name:
        return ""

    name = str(name).strip()

    # OCR에서 캡슐을 캡슬로 읽는 경우
    name = name.replace("캡슬", "캡슐")

    # 괄호가 닫히지 않아도 괄호 뒤 성분명은 비교에서 제외
    name = re.split(r"[\(\[\{]", name, maxsplit=1)[0]

    # 앞쪽 bullet 제거
    name = re.sub(r"^[\*\-\·•]+", "", name)

    # 끝쪽 용량 표기 제거
    # 예: 타이레놀정500mg -> 타이레놀정
    name = re.sub(
        r"\d+(?:\.\d+)?\s*(?:mg|MG|g|G|ml|mL|ML|밀리그램|그램|밀리리터)$",
        "",
        name,
    )

    # 공백 제거
    name = re.sub(r"\s+", "", name)

    # 한글 / 영문 / 숫자만 유지
    name = re.sub(
        r"[^가-힣A-Za-z0-9]",
        "",
        name,
    )

    return name.lower()


# ============================================================
# 제형 추출
# ============================================================

def get_medicine_form(name: str):
    """
    동일 제형끼리 우선 비교한다.
    """
    forms = [
        "연질캡슐",
        "캡슐",
        "현탁액",
        "점안액",
        "흡입제",
        "패취",
        "패치",
        "연고",
        "크림",
        "시럽",
        "과립",
        "겔",
        "주사",
        "정",
    ]

    normalized = normalize_medicine_name(name)

    for form in forms:
        if normalized.endswith(form):
            return form

    return None


# ============================================================
# 후보 구성
# ============================================================

def _build_candidates(ocr_name: str):
    """
    OCR 결과의 제형이 확인되면 동일 제형만 비교한다.
    """
    if not MEDICINE_DATABASE:
        return []

    form = get_medicine_form(ocr_name)

    if form is None:
        return MEDICINE_DATABASE

    same_form = [
        medicine
        for medicine in MEDICINE_DATABASE
        if get_medicine_form(medicine) == form
    ]

    return same_form if same_form else MEDICINE_DATABASE


# ============================================================
# 가장 비슷한 실제 의약품명 검색
# ============================================================

def match_medicine_name(ocr_name: str):
    """
    반환 형식:

    {
        "originalName": "배포스타바정",
        "matchedName": "베포스타비정",
        "suggestedName": "베포스타비정",
        "score": 72.0,
        "autoCorrected": False,
        "needsReview": True,
        "isValidMedicine": True
    }

    기준:
    - 90점 이상: 자동 교정
    - 65~89.9점: 실제 의약품 후보로 인정하되 사용자 확인
    - 65점 미만: 약이 아닐 가능성이 높아 파서에서 제외
    """
    original = str(ocr_name or "").strip()
    normalized_ocr = normalize_medicine_name(original)

    if not normalized_ocr:
        return {
            "originalName": original,
            "matchedName": original,
            "suggestedName": None,
            "score": 0.0,
            "autoCorrected": False,
            "needsReview": True,
            "isValidMedicine": False,
        }

    candidates = _build_candidates(original)

    if not candidates:
        return {
            "originalName": original,
            "matchedName": original,
            "suggestedName": None,
            "score": 0.0,
            "autoCorrected": False,
            "needsReview": True,
            "isValidMedicine": False,
        }

    normalized_map = {}

    for medicine in candidates:
        normalized = normalize_medicine_name(medicine)

        if normalized:
            normalized_map[normalized] = medicine

    if not normalized_map:
        return {
            "originalName": original,
            "matchedName": original,
            "suggestedName": None,
            "score": 0.0,
            "autoCorrected": False,
            "needsReview": True,
            "isValidMedicine": False,
        }

    # WRatio는 OCR 오탈자에 비교적 유연하다.
    result = process.extractOne(
        normalized_ocr,
        normalized_map.keys(),
        scorer=fuzz.WRatio,
    )

    if result is None:
        return {
            "originalName": original,
            "matchedName": original,
            "suggestedName": None,
            "score": 0.0,
            "autoCorrected": False,
            "needsReview": True,
            "isValidMedicine": False,
        }

    matched_normalized = result[0]
    score = float(result[1])
    matched_name = normalized_map[matched_normalized]

    # 짧은 OCR 문자열은 우연히 높은 점수가 나기 쉬우므로 자동교정 금지
    too_short = len(normalized_ocr) < 4

    # 90점 이상만 자동교정
    if score >= 90 and not too_short:
        return {
            "originalName": original,
            "matchedName": matched_name,
            "suggestedName": matched_name,
            "score": round(score, 1),
            "autoCorrected": True,
            "needsReview": False,
            "isValidMedicine": True,
        }

    # OCR 오탈자가 있는 실제 약 이름 후보
    if score >= 65:
        return {
            "originalName": original,
            "matchedName": matched_name,
            "suggestedName": matched_name,
            "score": round(score, 1),
            "autoCorrected": False,
            "needsReview": True,
            "isValidMedicine": True,
        }

    # 유사도가 너무 낮으면 약으로 채택하지 않음
    return {
        "originalName": original,
        "matchedName": matched_name,
        "suggestedName": matched_name,
        "score": round(score, 1),
        "autoCorrected": False,
        "needsReview": True,
        "isValidMedicine": False,
    }
