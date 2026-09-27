import re

from services.drug_match_service import match_medicine_name


# ============================================================
# 처방전에서 약 이름으로 인정할 제형
# ============================================================

MEDICINE_FORM_PATTERN = (
    r"(?:"
    r"연질캡슐|"
    r"캡슐|"
    r"캡슬|"      # OCR에서 '캡슐'을 '캡슬'로 읽는 경우 대응
    r"현탁액|"
    r"점안액|"
    r"흡입제|"
    r"패취|"
    r"패치|"
    r"연고|"
    r"크림|"
    r"시럽|"
    r"과립|"
    r"겔|"
    r"주사|"
    r"정"
    r")"
)


# 약 이름으로 잘못 인식하면 안 되는 문구
EXCLUDE_WORDS = {
    "약품명",
    "약품사진",
    "복약안내",
    "주의사항",
    "투약량",
    "투여량",
    "투약횟수",
    "투여횟수",
    "투약일수",
    "투여일수",
    "복용법",
    "조제약",
    "처방전",
    "환자",
    "성명",
    "나이",
    "환자정보",
    "조제일자",
    "처방일자",
    "영수증",
    "금액",
    "부담금",
    "수납",
    "현금영수증",
    "사업자",
    "등록번호",
    "소재지",
    "주소",
    "발행기관",
    "병원",
    "의원",
    "약제비",
    "보관",
    "효능",
    "계산",
}


# ============================================================
# 기본 정리
# ============================================================

def normalize_prescription_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace("\t", " ")

    # OCR에서 0,5처럼 읽힌 소수점 보정
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)

    # 세로줄 문자 통일
    text = text.replace("│", "|")
    text = text.replace("｜", "|")

    # 여러 공백 정리
    text = re.sub(r"[ ]+", " ", text)

    # 과도한 줄바꿈 정리
    text = re.sub(r"\n{2,}", "\n", text)

    return text.strip()


# ============================================================
# 숫자 변환
# ============================================================

def _safe_int(value, default=None):
    try:
        if value is None:
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _safe_float(value, default=None):
    try:
        if value is None:
            return default

        value = str(value).strip()

        # OCR에서 0.5 → 05로 읽는 경우 보정
        if value == "05":
            return 0.5

        return float(value)
    except (ValueError, TypeError):
        return default


# ============================================================
# 복약 횟수 찾기
# ============================================================

def find_daily_count(text: str):
    patterns = [
        r"1\s*일\s*(?:투여|투약)?\s*횟수\s*[:：]?\s*(\d+)",
        r"1\s*일\s*(\d+)\s*회",
        r"(?:투약|투여)\s*횟수\s*[:：]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if not match:
            continue

        value = _safe_int(match.group(1))

        if value is not None and 1 <= value <= 6:
            return value

    return None


# ============================================================
# 복용 일수 찾기
# ============================================================

def find_period(text: str):
    patterns = [
        r"(\d+)\s*일\s*분",
        r"총\s*(?:투약|투여)\s*일수\s*[:：]?\s*(\d+)",
        r"(?:투약|투여)\s*일수\s*[:：]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if not match:
            continue

        value = _safe_int(match.group(1))

        if value is not None and 1 <= value <= 365:
            return value

    return None


# ============================================================
# 1회 투약량 찾기
# ============================================================

def find_dosage(text: str):
    patterns = [
        (
            r"1\s*회\s*"
            r"(?:투약|투여)?\s*량"
            r"\s*[:：]?\s*"
            r"([0-9]+(?:\.[0-9]+)?)"
        ),
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return _safe_float(match.group(1))

    return None


# ============================================================
# 식전 / 식후 찾기
# ============================================================

def find_timing(text: str):
    compact = re.sub(r"\s+", "", text)

    if "식전30분" in compact:
        return "식전 30분"

    if "식후30분" in compact:
        return "식후 30분"

    if "식후즉시" in compact:
        return "식후 즉시"

    if "취침전" in compact:
        return "취침 전"

    if "식후" in compact:
        return "식후"

    if "식전" in compact:
        return "식전"

    return None


# ============================================================
# 표의 숫자 열 찾기
# ============================================================

def find_row_values(text: str):
    """
    예:
    메드닌정 1 3 5
    메드닌정 0.5 3 5
    메드닌정 | 1 | 3 | 5
    메드닌정 1 3 5 15
    """

    if not text:
        return None

    normalized = text.replace("|", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # 1 3 5 15 → 투약량 / 횟수 / 일수 / 총량
    match_four = re.search(
        r"([0-9]+(?:\.[0-9]+)?)"
        r"\s+"
        r"([1-6])"
        r"\s+"
        r"([1-9][0-9]{0,2})"
        r"\s+"
        r"([0-9]+(?:\.[0-9]+)?)"
        r"\s*$",
        normalized,
    )

    if match_four:
        dosage = _safe_float(match_four.group(1))
        daily_count = _safe_int(match_four.group(2))
        period = _safe_int(match_four.group(3))

        if (
            dosage is not None
            and daily_count is not None
            and period is not None
        ):
            return {
                "dosage": dosage,
                "dailyCount": daily_count,
                "period": period,
            }

    # 1 3 5 / 0.5 3 5
    match_three = re.search(
        r"([0-9]+(?:\.[0-9]+)?)"
        r"\s+"
        r"([1-6])"
        r"\s+"
        r"([1-9][0-9]{0,2})"
        r"\s*$",
        normalized,
    )

    if match_three:
        dosage = _safe_float(match_three.group(1))
        daily_count = _safe_int(match_three.group(2))
        period = _safe_int(match_three.group(3))

        if (
            dosage is not None
            and daily_count is not None
            and period is not None
        ):
            return {
                "dosage": dosage,
                "dailyCount": daily_count,
                "period": period,
            }

    return None


# ============================================================
# 약 이름 추출
# ============================================================

def extract_medicine_name(line: str):
    if not line:
        return None

    work = line.strip()

    # 앞쪽 bullet 제거
    work = re.sub(r"^[\*\-\•·]+\s*", "", work)

    # 앞쪽 의약품 코드 제거
    work = re.sub(r"^\d{6,12}\s+", "", work)

    compact = re.sub(r"\s+", "", work)

    # 환자명/주소/금액/헤더 등 명백한 비약품 문맥 제거
    if any(word in compact for word in EXCLUDE_WORDS):
        return None

    medicine_pattern = re.compile(
        r"("
        r"[가-힣A-Za-z0-9]"
        r"[가-힣A-Za-z0-9·\-\+\._]*"
        + MEDICINE_FORM_PATTERN +
        r"(?:"
        r"\d+(?:\.\d+)?"
        r"(?:mg|MG|g|G|ml|mL|ML)?"
        r")?"
        r")"
    )

    matches = list(medicine_pattern.finditer(work))

    if not matches:
        return None

    for match in matches:
        name = match.group(1).strip()

        if len(name) < 2:
            continue

        if len(name) > 50:
            continue

        # OCR 오타 보정: 캡슬 → 캡슐
        if name.endswith("캡슬"):
            name = name[:-2] + "캡슐"

        return name

    return None


# ============================================================
# 중복 제거
# ============================================================

def remove_duplicate_medicines(medicines):
    result = []
    seen = set()

    for medicine in medicines:
        name = medicine.get("medicineName", "").strip()

        if not name:
            continue

        key = re.sub(r"\s+", "", name).lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(medicine)

    return result


# ============================================================
# 실제 처방전 파싱
# ============================================================

def parse_prescription_text(text: str):
    if not text:
        return []

    normalized_text = normalize_prescription_text(text)

    lines = [
        line.strip()
        for line in normalized_text.split("\n")
        if line.strip()
    ]

    if not lines:
        return []

    # --------------------------------------------------------
    # 1. 처방전 전체 공통 정보
    # --------------------------------------------------------

    global_daily_count = find_daily_count(normalized_text)
    global_period = find_period(normalized_text)
    global_dosage = find_dosage(normalized_text)
    global_timing = find_timing(normalized_text)

    print("[PARSE] global daily_count =", global_daily_count)
    print("[PARSE] global period =", global_period)
    print("[PARSE] global dosage =", global_dosage)
    print("[PARSE] global timing =", global_timing)

    # --------------------------------------------------------
    # 2. 약 이름 후보 행 찾기
    # --------------------------------------------------------

    medicine_positions = []

    for index, line in enumerate(lines):
        name = extract_medicine_name(line)

        if name:
            medicine_positions.append(
                {
                    "index": index,
                    "name": name,
                    "line": line,
                }
            )

    medicines = []

    # --------------------------------------------------------
    # 3. 약별 복약정보 연결
    # --------------------------------------------------------

    for position_index, position in enumerate(medicine_positions):
        line_index = position["index"]
        medicine_name = position["name"]
        current_line = position["line"]

        dosage = None
        daily_count = None
        period = None
        timing = None

        # 같은 줄의 표 숫자
        row_values = find_row_values(current_line)

        if row_values:
            dosage = row_values["dosage"]
            daily_count = row_values["dailyCount"]
            period = row_values["period"]

        # 다음 약이 나오기 전까지 최대 2줄 확인
        nearby_lines = [current_line]

        next_medicine_index = (
            medicine_positions[position_index + 1]["index"]
            if position_index + 1 < len(medicine_positions)
            else len(lines)
        )

        end_index = min(
            line_index + 3,
            next_medicine_index,
        )

        for j in range(line_index + 1, end_index):
            nearby_lines.append(lines[j])

        nearby_text = " ".join(nearby_lines)

        if dosage is None:
            dosage = find_dosage(nearby_text)

        if daily_count is None:
            daily_count = find_daily_count(nearby_text)

        if period is None:
            period = find_period(nearby_text)

        timing = find_timing(nearby_text)

        # 약별 값이 없으면 처방전 전체 공통값 사용
        if dosage is None:
            dosage = global_dosage if global_dosage is not None else 1.0

        if daily_count is None:
            daily_count = (
                global_daily_count
                if global_daily_count is not None
                else 0
            )

        if period is None:
            period = (
                global_period
                if global_period is not None
                else 0
            )

        if timing is None:
            timing = (
                global_timing
                if global_timing is not None
                else ""
            )

        # ----------------------------------------------------
        # 4. 실제 의약품명과 fuzzy matching
        # ----------------------------------------------------

        match_result = match_medicine_name(medicine_name)

        # 실제 의약품 DB와 유사도가 너무 낮으면
        # 환자명/주소/금액 등 OCR 오탐으로 보고 제외한다.
        if not bool(
            match_result.get(
                "isValidMedicine",
                False,
            )
        ):
            print(
                "[DRUG MATCH] 제외:",
                medicine_name,
                "score=",
                match_result.get("score", 0.0),
            )
            continue

        matched_name = str(
            match_result.get(
                "matchedName",
                medicine_name,
            )
        ).strip()

        match_score = float(
            match_result.get(
                "score",
                0.0,
            )
            or 0.0
        )

        auto_corrected = bool(
            match_result.get(
                "autoCorrected",
                False,
            )
        )

        match_needs_review = bool(
            match_result.get(
                "needsReview",
                True,
            )
        )

        # 자동 보정 기준을 통과한 경우에만 실제 표시 이름을 교정한다.
        # 애매한 후보는 원래 OCR 이름을 유지하고 suggestedMedicineName으로 전달.
        corrected_name = (
            matched_name
            if auto_corrected and matched_name
            else medicine_name
        )

        needs_review = (
            daily_count <= 0
            or period <= 0
            or match_needs_review
        )

        total_count = 0

        if (
            daily_count > 0
            and period > 0
            and dosage > 0
        ):
            total_count = (
                dosage
                * daily_count
                * period
            )

        medicine_data = {
            "medicineName": corrected_name,
            "ocrMedicineName": medicine_name,
            "medicineMatchScore": round(match_score, 1),
            "medicineAutoCorrected": auto_corrected,
            "dailyCount": daily_count,
            "dosage": dosage,
            "timing": timing,
            "period": period,
            "totalCount": total_count,
            "needsReview": needs_review,
            "rawLine": current_line,
        }

        if (
            matched_name
            and matched_name != corrected_name
            and match_score > 0
        ):
            medicine_data[
                "suggestedMedicineName"
            ] = matched_name

        medicines.append(medicine_data)

    medicines = remove_duplicate_medicines(medicines)

    print("[PARSE] medicines =", medicines)

    return medicines
