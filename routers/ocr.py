import json
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form

from firebase_config import db
from services.ocr_service import extract_text_from_image
from services.parse_service import parse_prescription_text
from services.schedule_service import generate_times
from services.allergy_service import check_allergy_warning


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


def _normalize_medicine(medicine):
    """
    parse_service 또는 Flutter에서 받은 약 정보를
    동일한 구조로 정리한다.
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

    # parse_service에서 이미 검토 필요 판정을 내렸다면 유지
    upstream_needs_review = bool(
        medicine.get(
            "needsReview",
            False,
        )
    )

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
    }

    # OCR 약 이름 보정 정보 유지
    optional_fields = [
        "ocrMedicineName",
        "medicineMatchScore",
        "medicineAutoCorrected",
        "suggestedMedicineName",
        "rawLine",
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
    잘못 읽은 OCR 값을 1일 1회/1일분으로 몰래 보정하지 않고,
    최종 저장 전에 사용자가 확인하도록 막는다.
    """

    errors = []

    for index, medicine in enumerate(medicines):
        name = str(
            medicine.get(
                "medicineName",
                "",
            )
        ).strip()

        daily_count = _to_int(
            medicine.get(
                "dailyCount"
            ),
            0,
        )

        dosage = _to_float(
            medicine.get(
                "dosage"
            ),
            0.0,
        )

        period = _to_int(
            medicine.get(
                "period"
            ),
            0,
        )

        medicine_errors = []

        if not name:
            medicine_errors.append(
                "약 이름"
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
                "message":
                    "이미지 파일이 비어 있습니다.",
                "medicines": [],
            }

        raw_text = extract_text_from_image(
            contents
        )

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
                "message":
                    "처방전에서 글자를 인식하지 못했습니다.",
                "filename":
                    file.filename,
                "rawText":
                    "",
                "medicineCount":
                    0,
                "needsReviewCount":
                    0,
                "medicines":
                    [],
            }

        parsed_medicines = (
            parse_prescription_text(
                raw_text
            )
        )

        if parsed_medicines is None:
            parsed_medicines = []

        medicines = (
            _normalize_medicines(
                parsed_medicines
            )
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
            "medicineCount":
                len(medicines),
            "needsReviewCount":
                sum(
                    1
                    for medicine in medicines
                    if medicine.get(
                        "needsReview",
                        False,
                    )
                ),
            "medicines":
                medicines,
            "message":
                (
                    "처방전 분석 완료"
                    if medicines
                    else
                    "글자는 인식했지만 약 정보를 찾지 못했습니다."
                ),
        }

    except Exception as e:
        print(
            "OCR 처리 오류:",
            str(e),
        )

        return {
            "success": False,
            "message":
                "OCR 처리 중 오류가 발생했습니다.",
            "error":
                str(e),
            "medicines":
                [],
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
                "message":
                    "medicinesJson은 배열 형식이어야 합니다.",
            }

        if len(medicines) == 0:
            return {
                "success": False,
                "message":
                    "등록할 약이 없습니다.",
            }

        medicines = (
            _normalize_medicines(
                medicines
            )
        )

        if len(medicines) == 0:
            return {
                "success": False,
                "message":
                    "유효한 약 정보가 없습니다.",
            }

        # --------------------------------
        # 2. 저장 전 필수 복약정보 검사
        # --------------------------------
        validation_errors = (
            _validate_medicines_for_save(
                medicines
            )
        )

        if validation_errors:
            return {
                "success": False,
                "message":
                    "복약 정보가 비어 있는 약이 있습니다. 수정 화면에서 확인해주세요.",
                "invalidMedicines":
                    validation_errors,
                "savedCount":
                    0,
            }

        # --------------------------------
        # 3. 원본 처방전 OCR 텍스트 보관
        # --------------------------------
        raw_text = ""
        filename = None

        if file is not None:
            filename = file.filename
            contents = await file.read()

            if contents:
                try:
                    raw_text = (
                        extract_text_from_image(
                            contents
                        )
                    )
                except Exception as e:
                    # 원본 OCR 재실행 실패가 최종 저장을 막지는 않음
                    print(
                        "원본 이미지 OCR 재실행 실패:",
                        str(e),
                    )
                    raw_text = ""

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

            daily_count = _to_int(
                medicine.get(
                    "dailyCount"
                ),
                0,
            )

            dosage = _to_float(
                medicine.get(
                    "dosage"
                ),
                0.0,
            )

            period = _to_int(
                medicine.get(
                    "period"
                ),
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

            times = generate_times(
                daily_count
            )

            total_count = (
                daily_count
                * period
                * dosage
            )

            allergy_warning = (
                check_allergy_warning(
                    user_id=userId,
                    medicine_name=
                        medicine_name,
                )
            )

            doc_ref = (
                db.collection(
                    "schedules"
                ).document()
            )

            schedule_data = {
                "userId":
                    userId,
                "medicineName":
                    medicine_name,
                "dailyCount":
                    daily_count,
                "times":
                    times,
                "dosage":
                    dosage,
                "timing":
                    timing,
                "startDate":
                    start_date,
                "endDate":
                    end_date,
                "period":
                    period,
                "totalCount":
                    total_count,
                "remainingCount":
                    total_count,
                "active":
                    True,
                "source":
                    (
                        "OCR"
                        if file is not None
                        else "MANUAL"
                    ),
                "createdAt":
                    datetime.now()
                    .isoformat(),
            }

            if allergy_warning:
                schedule_data[
                    "allergyWarning"
                ] = allergy_warning

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
                "message":
                    "저장할 수 있는 복약 일정이 없습니다.",
                "savedCount":
                    0,
                "schedules":
                    [],
            }

        return {
            "success": True,
            "message":
                "수정된 처방전 정보 및 복약 일정 저장 완료",
            "filename":
                filename,
            "rawText":
                raw_text,
            "medicineCount":
                len(medicines),
            "savedCount":
                len(saved_schedules),
            "schedules":
                saved_schedules,
        }

    except json.JSONDecodeError as e:
        print(
            "medicinesJson 파싱 오류:",
            str(e),
        )

        return {
            "success": False,
            "message":
                "약 정보 JSON 형식이 올바르지 않습니다.",
            "error":
                str(e),
        }

    except Exception as e:
        print(
            "OCR 저장 오류:",
            str(e),
        )

        return {
            "success": False,
            "message":
                "OCR 저장 처리 중 오류가 발생했습니다.",
            "error":
                str(e),
        }
