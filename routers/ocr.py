import json
import re
from rapidfuzz import fuzz
from typing import Optional
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, Form

from firebase_config import db
from services.ocr_service import extract_text_from_image
from services.drug_candidate_service import search_medicine_candidates
from services.parse_service import parse_prescription_text
from services.schedule_service import generate_times
from services.allergy_service import check_allergy_warning
from services.drug_match_service import (
    match_medicine_name,
    normalize_medicine_name,
)
from services.allergy_service import check_allergy_warning
from services.medicine_duplicate_service import find_duplicate_schedule
from services.allergy_service import check_allergy_warning
from services.stock_merge_service import save_or_merge_stock

router = APIRouter(
    prefix="/ocr",
    tags=["OCR"],
)


# ============================================================
# 공통 변환 함수
# ============================================================

def _to_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _to_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

def _generate_user_times(
    user_id,
    daily_count,
):
    morning = "08:30"
    lunch = "13:30"
    dinner = "19:30"

    try:
        user_doc = (
            db.collection("users")
            .document(user_id)
            .get()
        )

        if user_doc.exists:
            user = (
                user_doc.to_dict()
                or {}
            )

            medication_times = (
                user.get(
                    "medicationTimes",
                    {},
                )
                or {}
            )

            morning = str(
                medication_times.get(
                    "morning",
                    morning,
                )
            )

            lunch = str(
                medication_times.get(
                    "lunch",
                    lunch,
                )
            )

            dinner = str(
                medication_times.get(
                    "dinner",
                    dinner,
                )
            )

    except Exception as e:
        print(
            "복약 시간 설정 조회 실패:",
            e,
        )

    if daily_count == 1:
        return [
            morning,
        ]

    if daily_count == 2:
        return [
            morning,
            dinner,
        ]

    if daily_count == 3:
        return [
            morning,
            lunch,
            dinner,
        ]

    # 4회 이상은 기존 함수 사용
    return generate_times(
        daily_count
    )


def _collect_unresolved_ocr_groups(
    name_groups,
    medicines,
):
    """
    위치별 OCR 후보 중 최종 약 목록에 포함되지 않은
    문자열을 별도 검토 대상으로 보관한다.

    의약품이 아닌 문구도 포함될 수 있으므로
    여기서 자동 등록하지 않는다.
    """
    results = []
    seen = set()

    def comparable(value):
        value = str(value or "").split("(")[0]
        return normalize_medicine_name(value)

    known_names = []

    for medicine in medicines:
        known_names.extend([
            medicine.get("medicineName", ""),
            medicine.get("ocrMedicineName", ""),
            medicine.get("suggestedMedicineName", ""),
        ])

    for group in name_groups:
        names = [
            str(name).strip()
            for name in group.get("names", [])
            if str(name).strip()
        ]

        # 다른 OCR 설정에서 반복 확인된 위치만 사용
        if group.get("sourceCount", 0) < 2:
            continue

        pending = []

        for name in names:
            normalized = comparable(name)

            if len(normalized) < 3:
                continue

            # 기존 약 이름과 유사한 인식 결과는
            # 별도의 미확인 항목으로 중복 표시하지 않는다.
            already_known = any(
                fuzz.ratio(
                    normalized,
                    comparable(known),
                ) >= 80
                for known in known_names
                if known
            )

            if already_known:
                continue

            if normalized not in seen:
                pending.append(name)
                seen.add(normalized)

        if not pending:
            continue

        results.append({
            "ocrNames": pending,
            "x": group.get("x"),
            "y": group.get("y"),
            "sourceCount": group.get("sourceCount"),
            "status": "needs_manual_search",
            "requiresConfirmation": True,
        })

    return results


def _merge_positioned_ocr_medicines(
    medicines,
    name_groups,
):
    """
    기존 파싱 결과에 위치 기반 OCR 후보를 보완한다.

    - 동일하게 보이는 약은 중복 추가하지 않음
    - 식약처 검색에서 확인된 이름을 우선
    - 서로 다른 숫자 표기는 자동 확정하지 않음
    - 새로 추가한 후보는 사용자 확인 필요
    """
    merged = list(medicines)

    def _numbers(name):
        return re.findall(
            r"\d+(?:\.\d+)?",
            normalize_medicine_name(name),
        )

    def _same_likely_medicine(a, b):
        a = normalize_medicine_name(a)
        b = normalize_medicine_name(b)

        # OCR 숫자가 서로 다르면 자동 중복 처리 금지
        if _numbers(a) != _numbers(b):
            return False

        return fuzz.ratio(a, b) >= 78

    for group in name_groups[:15]:
        names = list(dict.fromkeys(
            str(n).strip()
            for n in group.get("names", [])
            if str(n).strip()
        ))

        if not names:
            continue

        # 후보가 너무 많아지지 않도록 그룹당 최대 4개 검사
        evaluated = []

        for name in names[:4]:
            match_result = match_medicine_name(name)

            evaluated.append({
                "ocrName": name,
                "match": match_result,
            })

        # 실제 식약처에서 정확한 품목을 찾은 후보 우선
        evaluated.sort(
            key=lambda item: (
                bool(
                    item["match"].get("isValidMedicine")
                ),
                float(
                    item["match"].get("score") or 0
                ),
            ),
            reverse=True,
        )

        chosen = evaluated[0]
        match_result = chosen["match"]

        original_name = chosen["ocrName"]

        matched_name = str(
            match_result.get("matchedName")
            or original_name
        ).strip()

        score = float(
            match_result.get("score") or 0
        )

        exact = bool(
            match_result.get("autoCorrected")
        )

        # 서로 다른 숫자로 인식됐다면 반드시 사용자 확인
        normalized_numbers = {
            tuple(_numbers(name))
            for name in names
        }

        number_conflict = (
            len(normalized_numbers) > 1
        )

        # 다른 OCR 방식에서 같은 위치의 이름이 다르게
        # 읽혔다는 사실을 그대로 전달
        alternatives = [
            item["ocrName"]
            for item in evaluated
        ]

        possible_names = [
            original_name,
            matched_name,
            *alternatives,
        ]
        # 공식 후보 이름이 같은 품목을 가리키면
        # 위치 OCR 결과를 새 약으로 추가하지 않음
        normalized_matched = normalize_medicine_name(
            matched_name
        )

        same_official_candidate = next(
            (
                existing
                for existing in merged
                if normalize_medicine_name(
                    existing.get("suggestedMedicineName")
                    or existing.get("medicineName")
                    or ""
                ) == normalized_matched
            ),
            None,
        )

        if same_official_candidate is not None:
            same_official_candidate["ocrAlternatives"] = list(
                dict.fromkeys(
                    same_official_candidate.get(
                        "ocrAlternatives", []
                    ) + alternatives
                )
            )
            same_official_candidate["needsReview"] = True
            continue
        

        matched_existing = None

        for existing in merged:
            existing_names = [
                existing.get("medicineName", ""),
                existing.get("ocrMedicineName", ""),
            ]

            if any(
                _same_likely_medicine(a, b)
                for a in possible_names
                for b in existing_names
                if a and b
            ):
                matched_existing = existing
                break

        if matched_existing is not None:
            matched_existing["ocrAlternatives"] = list(
                dict.fromkeys(
                    matched_existing.get(
                        "ocrAlternatives", []
                    ) + alternatives
                )
            )

            if number_conflict:
                matched_existing["ocrNumberConflict"] = True
                matched_existing["needsReview"] = True

            # 기존 약 이름이 확인되지 않았지만
            # 다른 OCR 방식에서 식약처 품목을 찾은 경우
            if (
                not matched_existing.get("itemSeq")
                and match_result.get("isValidMedicine")
                and score >= 90
            ):
                matched_existing["suggestedMedicineName"] = (
                    matched_name
                )
                matched_existing["medicineMatchScore"] = score
                matched_existing["needsReview"] = True

                print(
                    "[OCR MERGE] 공식 약 이름 추천:",
                    matched_existing["medicineName"],
                    "->",
                    matched_name,
                )

            continue
       

        # 식약처에서 확인되지 않은 새로운 이름은
        # OCR 등록 목록에 자동 추가하지 않는다.

        # 다만 기존 파싱 결과에 이미 존재하는
        # 미확인 이름은 사용자 수정 대상으로 유지한다.
        if not bool(match_result.get("isValidMedicine")):
            print(
                "[OCR MERGE] 미확인 후보 추가 생략:",
                original_name,
            )
            continue

        # 실제 의약품 후보라도 점수가 낮으면 자동 추가하지 않음
        if score < 90:
            print(
                "[OCR MERGE] 낮은 유사도 후보 추가 생략:",
                original_name,
                score,
            )
            continue
        # 새로운 약 이름 후보는 항상 검토 대상으로 추가
        # 숫자 충돌 시 공식 이름으로 자동 변경하지 않음
        display_name = (
            matched_name
            if exact and not number_conflict
            else original_name
        )

        medicine = {
            "medicineName": display_name,
            "ocrMedicineName": original_name,
            "medicineMatchScore": score,
            "medicineAutoCorrected": False,
            "suggestedMedicineName": (
                matched_name
                if matched_name != display_name
                else None
            ),
            "itemSeq": None,
            "dailyCount": 0,
            "dosage": 1.0,
            "period": 0,
            "timing": "",
            "needsReview": True,
            "isStock": False,
            "asNeeded": False,
            "setSize": 0,
            "startDate": "",
            "endDate": "",
            "ocrAlternatives": alternatives,
            "ocrNumberConflict": number_conflict,
            "rawLine": original_name,
        }

        print(
            "[OCR MERGE] 후보 추가:",
            display_name,
            "대체 인식:",
            alternatives,
        )

        merged.append(medicine)

    return merged


def _remove_sentence_false_medicines(medicines):
    """
    일반 문장에서 잘못 추출된 약 이름을 제외한다.

    - 식약처 품목 매칭 및 후보가 모두 없는 경우만 검사
    - 'OO정에', 'OO정은' 등 문장의 일부인 경우 감지
    - 실제 약일 가능성이 남아 있는 경우에는 유지
    """
    import re

    result = []

    for medicine in medicines:
        name = str(
            medicine.get("ocrMedicineName")
            or medicine.get("medicineName")
            or ""
        ).strip()

        raw_line = str(
            medicine.get("rawLine")
            or ""
        ).strip()

        has_official_match = bool(
            medicine.get("itemSeq")
            or medicine.get("suggestedMedicineName")
            or medicine.get("medicineCandidates")
        )

        if has_official_match:
            result.append(medicine)
            continue

        # OCR이 일반 문장 중간의 '정'까지를
        # 약 이름으로 잘못 잘라낸 경우
        followed_by_particle = bool(
            name
            and re.search(
                re.escape(name)
                + r"(?:에|은|는|이|가|을|를|에서|으로)",
                raw_line,
            )
        )

        # 복용량이나 복용 횟수 형태가 있는지 확인
        has_dosage_evidence = bool(
            re.search(
                r"\d+\s*(?:정|캡슐|캡슬|캡술|캡숄|ml|mL)"
                r"\s*씩|\d+\s*회",
                raw_line,
            )
        )

        if followed_by_particle and not has_dosage_evidence:
            print(
                "[OCR FILTER] 문장 오인식 후보 제외:",
                name,
                "원문:",
                raw_line,
            )
            continue

        result.append(medicine)

    return result


def _attach_medicine_candidates(medicines):
    """
    미확정 약의 원래 이름과 OCR 대체 이름으로
    식약처 후보를 검색한다.

    후보는 추천만 하며 자동으로 약을 변경하지 않는다.
    """
    for medicine in medicines:
        medicine["medicineCandidates"] = []

        # 품목기준코드가 확정된 약은 그대로 유지
        if medicine.get("itemSeq"):
            continue

        # 이미 공식 약 이름 추천을 찾았다면
        # 기존 추천을 유지한다.
        if medicine.get("suggestedMedicineName"):
            continue

        original = str(
            medicine.get("ocrMedicineName")
            or medicine.get("medicineName")
            or ""
        ).strip()

        alternatives = [
            str(name).strip()
            for name in medicine.get(
                "ocrAlternatives", []
            )
            if str(name).strip()
        ]

        search_names = list(dict.fromkeys(
            [original] + alternatives
        ))

        found = {}

        for name in search_names[:5]:
            # 같은 약의 OCR 오인식만 비교한다.
            # 다른 약이 섞여 있을 가능성이 있으므로
            # 유사하지 않은 후보는 검색하지 않는다.
            original_normalized = (
                normalize_medicine_name(original)
            )
            name_normalized = (
                normalize_medicine_name(name)
            )

            if (
                name != original
                and fuzz.ratio(
                    original_normalized,
                    name_normalized
                ) < 75
            ):
                continue

            # 1. 전체 이름 정확 검색 및 부분 검색
            try:
                from services.drug_api_service import (
                    search_drug_by_name,
                )

                exact_results = search_drug_by_name(name)

            except Exception as e:
                print(
                    "[OCR CANDIDATE] 전체 검색 오류:",
                    name, str(e),
                )
                exact_results = []

            # 직접 검색 결과도 후보로 보관
            for drug in exact_results:
                item_name = str(
                    drug.get("itemName") or ""
                )
                item_seq = str(
                    drug.get("itemSeq") or ""
                )

                if not item_seq or not item_name:
                    continue

                base_name = (
                    item_name.split("(")[0].strip()
                )

                similarity = round(
                    fuzz.WRatio(
                        normalize_medicine_name(name),
                        normalize_medicine_name(base_name),
                    ),
                    1,
                )

                # 후보 오탐을 줄이기 위한 제한.
                # 실제 의약품 여부의 확신도를 뜻하지 않는다.
                if similarity < 70:
                    continue

                original_numbers = re.findall(
                    r"\d+(?:\.\d+)?",
                    original_normalized,
                )

                official_numbers = re.findall(
                    r"\d+(?:\.\d+)?",
                    normalize_medicine_name(base_name),
                )

                found[item_seq] = {
                    "itemName": item_name,
                    "itemSeq": item_seq,
                    "entpName": str(
                        drug.get("entpName") or ""
                    ),
                    "mainIngredient": str(
                        drug.get("mainIngredient") or ""
                    ),
                    "matchScore": similarity,
                    "numberConflict": (
                        bool(original_numbers)
                        and original_numbers
                        != official_numbers
                    ),
                    "requiresConfirmation": True,
                }

            # 2. 부분 검색 후보 추가
            partial_candidates = (
                search_medicine_candidates(
                    name,
                    limit=5,
                )
            )

            for candidate in partial_candidates:
                item_seq = candidate.get("itemSeq")

                if not item_seq:
                    continue

                # 다른 OCR 이름에서 찾았더라도
                # 표시할 비교 점수는 원래 OCR 이름 기준
                candidate = dict(candidate)

                official_base = str(
                    candidate.get("itemName") or ""
                ).split("(")[0]

                candidate["matchScore"] = round(
                    fuzz.WRatio(
                        original_normalized,
                        normalize_medicine_name(
                            official_base
                        ),
                    ),
                    1,
                )

                original_numbers = re.findall(
                    r"\d+(?:\.\d+)?",
                    original_normalized,
                )

                official_numbers = re.findall(
                    r"\d+(?:\.\d+)?",
                    normalize_medicine_name(
                        official_base
                    ),
                )

                candidate["numberConflict"] = (
                    bool(original_numbers)
                    and original_numbers != official_numbers
                )
                candidate["requiresConfirmation"] = True

                previous = found.get(item_seq)

                if (
                    previous is None
                    or candidate["matchScore"]
                    > previous["matchScore"]
                ):
                    found[item_seq] = candidate

        candidates = sorted(
            found.values(),
            key=lambda c: (
                not c.get("numberConflict", False),
                c.get("matchScore", 0),
            ),
            reverse=True,
        )[:5]

        medicine["medicineCandidates"] = candidates

        if candidates:
            medicine["needsReview"] = True

        print(
            "[OCR CANDIDATES]",
            original,
            "->",
            [c["itemName"] for c in candidates],
        )

    return medicines



def _normalize_medicine(medicine):
    """
    parse_service 또는 Flutter에서 받은 약 정보를
    동일한 구조로 정리한다.

    일반 복약약은 dailyCount/period/timing을 사용하고,
    상비약은 isStock/asNeeded/setSize를 유지한다.
    """

    medicine_name = str(
        medicine.get("medicineName", "")
    ).strip()

    daily_count = _to_int(
        medicine.get("dailyCount"),
        0,
    )

    dosage = _to_float(
        medicine.get("dosage"),
        1.0,
    )

    period = _to_int(
        medicine.get("period"),
        0,
    )

    timing = str(
        medicine.get("timing", "")
    ).strip()

    # 상비약 관련 필드
    is_stock = bool(
        medicine.get(
            "isStock",
            False,
        )
    )

    as_needed = bool(
        medicine.get(
            "asNeeded",
            is_stock,
        )
    )

    set_size = _to_int(
        medicine.get("setSize"),
        0,
    )

    start_date = str(
        medicine.get("startDate", "")
    ).strip()

    end_date = str(
        medicine.get("endDate", "")
    ).strip()

    # parse_service에서 이미 검토 필요 판정을 내렸다면 유지
    upstream_needs_review = bool(
        medicine.get(
            "needsReview",
            False,
        )
    )

    # 상비약은 정기 복약 횟수/일수가 없어도 정상이다.
    if is_stock:
        needs_review = (
            upstream_needs_review
            or not medicine_name
            or set_size <= 0
        )
    else:
        needs_review = (
            upstream_needs_review
            or not medicine_name
            or daily_count <= 0
            or period <= 0
        )

    result = {
        "medicineName": medicine_name,
        "dailyCount": daily_count,
        "dosage": dosage,
        "period": period,
        "timing": timing,
        "needsReview": needs_review,
        "isStock": is_stock,
        "asNeeded": as_needed,
        "setSize": set_size,
        "startDate": start_date,
        "endDate": end_date,
        # 약 DB/API에서 추출한 공식 복용량 정보
        "singleDoseText":
            str(
                medicine.get(
                    "singleDoseText",
                    "",
                )
                or ""
            ).strip(),

        "maxDailyDoseText":
            str(
                medicine.get(
                    "maxDailyDoseText",
                    "",
                )
                or ""
            ).strip(),

        "usage":
            str(
                medicine.get(
                    "usage",
                    "",
                )
                or ""
            ).strip(),
    }

    # OCR 약 이름 보정 정보 유지
    optional_fields = [
        "ocrMedicineName",
        "medicineMatchScore",
        "medicineAutoCorrected",
        "suggestedMedicineName",
        "rawLine",
        "itemSeq",
        "ocrAlternatives",
        "ocrNumberConflict",
    ]

    for field in optional_fields:
        if medicine.get(field) is not None:
            result[field] = medicine.get(field)

    return result


def _normalize_medicines(medicines):
    """
    파싱 결과 정리 + 빈 이름 제거 + 중복 제거
    """

    result = []
    seen = set()

    for medicine in medicines:
        if not isinstance(medicine, dict):
            continue

        normalized = _normalize_medicine(
            medicine
        )

        name = normalized[
            "medicineName"
        ].strip()

        if not name:
            continue

        key = name.replace(
            " ",
            "",
        ).lower()

        if key in seen:
            continue

        seen.add(key)
        result.append(normalized)

    return result


def _validate_medicines_for_save(medicines):
    """
    최종 저장 전 필수값을 확인한다.

    - 일반약: 1일 복용 횟수, 1회 복용량, 복용 일수 필요
    - 상비약: 정기 복약 횟수/일수는 필요 없고 보유 수량(setSize)만 필요
    """

    errors = []

    for index, medicine in enumerate(medicines):
        name = str(
            medicine.get(
                "medicineName",
                "",
            )
        ).strip()

        is_stock = bool(
            medicine.get(
                "isStock",
                False,
            )
        )

        medicine_errors = []

        if not name:
            medicine_errors.append(
                "약 이름"
            )

        # 상비약은 보유 수량만 필수
        if is_stock:
            set_size = _to_int(
                medicine.get("setSize"),
                0,
            )

            if set_size <= 0:
                medicine_errors.append(
                    "보유 수량"
                )

        # 일반약은 기존 복약정보가 필수
        else:
            daily_count = _to_int(
                medicine.get("dailyCount"),
                0,
            )

            dosage = _to_float(
                medicine.get("dosage"),
                0.0,
            )

            period = _to_int(
                medicine.get("period"),
                0,
            )

            if daily_count <= 0:
                medicine_errors.append(
                    "1일 복용 횟수"
                )

            if dosage <= 0:
                medicine_errors.append(
                    "1회 복용량"
                )

            if period <= 0:
                medicine_errors.append(
                    "복용 일수"
                )

        if medicine_errors:
            errors.append(
                {
                    "index": index,
                    "medicineName": name,
                    "missingFields": medicine_errors,
                }
            )

    return errors


# ============================================================
# 1. OCR 분석만 하는 API
# ============================================================

@router.post("/prescription")
async def ocr_prescription(
    file: UploadFile = File(...)
):
    try:
        contents = await file.read()

        if not contents:
            return {
                "success": False,
                "message": "이미지 파일이 비어 있습니다.",
                "medicines": [],
            }

        ocr_result = extract_text_from_image(
            contents,
            include_name_candidates=True,
        )

        raw_text = ocr_result["text"]
        name_groups = ocr_result["nameGroups"]

        print(
            "========== OCR RAW TEXT =========="
        )
        print(raw_text)
        print(
            "=================================="
        )

        if not raw_text or not raw_text.strip():
            return {
                "success": False,
                "message": "처방전에서 글자를 인식하지 못했습니다.",
                "filename": file.filename,
                "rawText": "",
                "medicineCount": 0,
                "needsReviewCount": 0,
                "medicines": [],
            }

        parsed_medicines = (
            parse_prescription_text(
                raw_text
            )
        )

        if parsed_medicines is None:
            parsed_medicines = []

        # 여러 OCR 방식의 위치 기반 약 이름을 통합
        parsed_medicines = _merge_positioned_ocr_medicines(
            parsed_medicines,
            name_groups,
        )

        medicines = (
            _normalize_medicines(
                parsed_medicines
            )
        )
        # 식약처 검색 실패 의약품에 후보 목록 추가
        medicines = _attach_medicine_candidates(
            medicines
        )
        # 문장 일부를 약 이름으로 잘못 인식한 경우 제외
        medicines = _remove_sentence_false_medicines(
            medicines
        )
        # 최종 등록 후보에 포함되지 않은 OCR 이름 보존
        unresolved_groups = _collect_unresolved_ocr_groups(
            name_groups,
            medicines,
        )

        print("========== 미확인 OCR 후보 ==========")

        for group in unresolved_groups:
            print(
                "[OCR UNRESOLVED]",
                group["ocrNames"],
                "position=",
                (group["x"], group["y"]),
            )

        print(
            "========== PARSED MEDICINES =========="
        )

        for medicine in medicines:
            print(medicine)

        print(
            "======================================"
        )

        return {
            "success": True,
            "filename": file.filename,
            "rawText": raw_text,
            "unresolvedOcrGroups": unresolved_groups,
            "unresolvedOcrGroupCount": len(unresolved_groups),
            "medicineCount": len(medicines),
            "needsReviewCount": sum(
                1
                for medicine in medicines
                if medicine.get(
                    "needsReview",
                    False,
                )
            ),
            "medicines": medicines,
            "message": (
                "처방전 분석 완료"
                if medicines
                else "글자는 인식했지만 약 정보를 찾지 못했습니다."
            ),
        }

    except Exception as e:
        print(
            "OCR 처리 오류:",
            str(e),
        )

        return {
            "success": False,
            "message": "OCR 처리 중 오류가 발생했습니다.",
            "error": str(e),
            "medicines": [],
        }


# ============================================================
# 2. 사용자가 수정한 최종 OCR 결과 저장
# ============================================================

@router.post("/prescription/save")
async def ocr_prescription_and_save(
    userId: str = Form(...),
    medicinesJson: str = Form(...),
    file: Optional[UploadFile] = File(None),
):
    try:
        # --------------------------------
        # 1. Flutter에서 보낸 최종 약 정보
        # --------------------------------
        medicines = json.loads(
            medicinesJson
        )

        if not isinstance(
            medicines,
            list,
        ):
            return {
                "success": False,
                "message": "medicinesJson은 배열 형식이어야 합니다.",
            }

        if len(medicines) == 0:
            return {
                "success": False,
                "message": "등록할 약이 없습니다.",
            }


        # 처방전의 그룹 정보를 정규화 전에 보존
        prescription_group_id = next(
            (
                str(m.get("prescriptionGroupId") or "").strip()
                for m in medicines
                if isinstance(m, dict)
                and str(m.get("prescriptionGroupId") or "").strip()
            ),
            "",
        )

        prescription_category = next(
            (
                str(m.get("prescriptionCategory") or "").strip()
                for m in medicines
                if isinstance(m, dict)
                and str(m.get("prescriptionCategory") or "").strip()
            ),
            "",
        )

        # 동일한 저장 요청에 포함된 처방약은 같은 그룹
        if not prescription_group_id:
            prescription_group_id = str(uuid4())

        if not prescription_category:
            prescription_category = "미분류 처방전"

        medicines = _normalize_medicines(medicines)

        # 정규화 과정에서 그룹 필드가 제거되어도 복원
        for medicine in medicines:
            if medicine.get("isStock") is True:
                continue

            medicine["prescriptionGroupId"] = prescription_group_id
            medicine["prescriptionCategory"] = prescription_category

        print(
            "[OCR SAVE] 처방전 그룹:",
            prescription_group_id,
            prescription_category,
        )


        if len(medicines) == 0:
            return {
                "success": False,
                "message": "유효한 약 정보가 없습니다.",
            }

        # --------------------------------
        # 2. 저장 전 필수 정보 검사
        # --------------------------------
        validation_errors = (
            _validate_medicines_for_save(
                medicines
            )
        )

        if validation_errors:
            return {
                "success": False,
                "message": "필수 약 정보가 비어 있습니다. 수정 화면에서 확인해주세요.",
                "invalidMedicines": validation_errors,
                "savedCount": 0,
            }

    
        # =========================================
        # 기존 정기 복약약과 중복 등록 검사
        # 저장하기 전에 모든 의약품을 검사
        # =========================================
        duplicate_medicines = []

        for medicine in medicines:
            # 상비약은 기존 재고 합산 로직을 유지
            if medicine.get("isStock") is True:
                continue

            item_seq = str(
                medicine.get("itemSeq") or ""
            ).strip()

            if not item_seq:
                return {
                    "success": False,
                    "message": (
                        "품목기준코드가 없어 중복 검사를 "
                        "완료할 수 없습니다. "
                        "의약품을 확인해주세요."
                    ),
                    "savedCount": 0,
                }

            start_date = str(
                medicine.get("startDate") or ""
            ).strip()

            end_date = str(
                medicine.get("endDate") or ""
            ).strip()

            try:
                duplicate = find_duplicate_schedule(
                    user_id=userId,
                    item_seq=item_seq,
                    start_date=start_date,
                    end_date=end_date,
                )

            except ValueError as e:
                return {
                    "success": False,
                    "message": str(e),
                    "savedCount": 0,
                }

            if duplicate:
                duplicate_medicines.append({
                    "medicineName": medicine.get(
                        "medicineName"
                    ),
                    "itemSeq": item_seq,
                    "existingScheduleId": duplicate[
                        "scheduleId"
                    ],
                    "reason": duplicate["reason"],
                })

        if duplicate_medicines:
            return {
                "success": False,
                "message": (
                    "이미 등록된 동일 의약품의 "
                    "복용 기간과 겹칩니다."
                ),
                "duplicateMedicines": duplicate_medicines,
                "savedCount": 0,
            }
   
        # 최종 저장에서는 OCR을 다시 실행하지 않음
        # Flutter에서 전달한 수정된 약 정보를 그대로 사용
        raw_text = ""

        filename = (
            file.filename
            if file is not None
            else None
        )

        print(
            "[OCR SAVE] 재분석 없이 최종 약 정보 저장:",
            len(medicines),
            "개",
        )

        saved_schedules = []

        # --------------------------------
        # 4. 최종 수정값을 Firestore에 저장
        # --------------------------------
        for medicine in medicines:
            medicine_name = str(
                medicine.get(
                    "medicineName",
                    "",
                )
            ).strip()
            prescription_group_id = str(
                medicine.get("prescriptionGroupId") or ""
            ).strip()

            prescription_category = str(
                medicine.get("prescriptionCategory") or ""
            ).strip()

            if not medicine_name:
                continue

            is_stock = bool(
                medicine.get(
                    "isStock",
                    False,
                )
            )

            as_needed = bool(
                medicine.get(
                    "asNeeded",
                    is_stock,
                )
            )

            set_size = _to_int(
                medicine.get("setSize"),
                0,
            )

            # =================================
            # A. 상비약 / 필요 시 복용
            # =================================
            single_dose_text = str(
                medicine.get(
                    "singleDoseText",
                    "",
                )
                or ""
            ).strip()

            max_daily_dose_text = str(
    medicine.get(
        "maxDailyDoseText",
        "",
    )
    or ""
).strip()

            usage_text = str(
                medicine.get(
                    "usage",
                    "",
                )
                or ""
            ).strip()
     

            item_seq = str(
                medicine.get(
                    "itemSeq",
                    ""
                )
            ).strip()
            if is_stock:
                if set_size <= 0:
                    continue

                
                # =================================
                # itemSeq 기반 상비약 중복 검사 및 합산
                # =================================

                try:
                    allergy_warning = check_allergy_warning(
                        user_id=userId,
                        medicine_name=medicine_name,
                        item_seq=item_seq,
                    )

                    result = save_or_merge_stock(
                        user_id=userId,
                        medicine_name=medicine_name,
                        item_seq=item_seq,
                        add_quantity=set_size,
                        single_dose_text=single_dose_text,
                        max_daily_dose_text=max_daily_dose_text,
                        usage=usage_text,
                        allergy_warning=allergy_warning,
                    )

                except ValueError as e:
                    return {
                        "success": False,
                        "message": str(e),
                        "savedCount": len(saved_schedules),
                        "schedules": saved_schedules,
                    }

                stock_data = result["stockData"].copy()

                stock_data["merged"] = result["merged"]
                stock_data["addedCount"] = result["addedCount"]

                saved_schedules.append(stock_data)
                continue


            # =================================
            # B. 일반 정기 복약약
            # =================================
            daily_count = _to_int(
                medicine.get("dailyCount"),
                0,
            )

            dosage = _to_float(
                medicine.get("dosage"),
                0.0,
            )

            period = _to_int(
                medicine.get("period"),
                0,
            )

            timing = str(
                medicine.get(
                    "timing",
                    "",
                )
            ).strip()

            start_date = str(
                medicine.get(
                    "startDate",
                    "",
                )
            ).strip()

            end_date = str(
                medicine.get(
                    "endDate",
                    "",
                )
            ).strip()

            if not timing:
                timing = "정보 없음"

            times = _generate_user_times(
                userId,
                daily_count,
            )

            total_count = (
                daily_count
                * period
                * dosage
            )

            doc_ref = (
                db.collection(
                    "schedules"
                ).document()
            )

            schedule_data = {
                "userId": userId,
                "medicineName": medicine_name,
                "isStock": False,
                "asNeeded": as_needed,
                "dailyCount": daily_count,
                "times": times,
                "dosage": dosage,
                "timing": timing,
                "startDate": start_date,
                "endDate": end_date,
                "period": period,
                "totalCount": total_count,
                "remainingCount": total_count,
                "active": True,
                "source": (
                    "OCR"
                    if file is not None
                    else "MANUAL"
                ),
                "createdAt": datetime.now().isoformat(),
                "itemSeq": item_seq,
                "prescriptionGroupId": prescription_group_id,
                "prescriptionCategory": prescription_category,
                "safetyCheckStatus": "not_checked",
            }


            doc_ref.set(
                schedule_data
            )

            schedule_data[
                "scheduleId"
            ] = doc_ref.id

            saved_schedules.append(
                schedule_data
            )

        if len(saved_schedules) == 0:
            return {
                "success": False,
                "message": "저장할 수 있는 복약 일정이 없습니다.",
                "savedCount": 0,
                "schedules": [],
            }

        return {
            "success": True,
            "message": "수정된 처방전 정보 및 복약 일정 저장 완료",
            "filename": filename,
            "rawText": raw_text,
            "medicineCount": len(medicines),
            "savedCount": len(saved_schedules),
            "schedules": saved_schedules,
        }

    except json.JSONDecodeError as e:
        print(
            "medicinesJson 파싱 오류:",
            str(e),
        )

        return {
            "success": False,
            "message": "약 정보 JSON 형식이 올바르지 않습니다.",
            "error": str(e),
        }

    except Exception as e:
        print(
            "OCR 저장 오류:",
            str(e),
        )

        return {
            "success": False,
            "message": "OCR 저장 처리 중 오류가 발생했습니다.",
            "error": str(e),
        }
