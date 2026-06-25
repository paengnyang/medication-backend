from fastapi import APIRouter, UploadFile, File, Form
from datetime import datetime

from firebase_config import db
from services.ocr_service import extract_text_from_image
from services.parse_service import parse_prescription_text
from services.schedule_service import generate_times

router = APIRouter(
    prefix="/ocr",
    tags=["OCR"]
)

# OCR 분석만 하는 API
@router.post("/prescription")
async def ocr_prescription(file: UploadFile = File(...)):
    try:
        # 업로드된 이미지 파일 읽기
        contents = await file.read()

        # OCR 실행
        raw_text = extract_text_from_image(contents)

        # OCR 텍스트 파싱
        medicines = parse_prescription_text(raw_text)

        return {
            "success": True,
            "filename": file.filename,
            "rawText": raw_text,
            "medicines": medicines
        }

    except Exception as e:
        return {
            "success": False,
            "message": "OCR 처리 중 오류가 발생했습니다.",
            "error": str(e)
        }

# OCR 결과를 schedules 컬렉션에 바로 저장하는 API
@router.post("/prescription/save")
async def ocr_prescription_and_save(
    userId: str = Form(...),
    startDate: str = Form(...),
    endDate: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        # 1. 이미지 파일 읽기
        contents = await file.read()

        # 2. OCR 실행
        raw_text = extract_text_from_image(contents)

        # 3. OCR 텍스트 파싱
        medicines = parse_prescription_text(raw_text)

        saved_schedules = []

        # 4. 파싱된 약 정보를 schedules 컬렉션에 저장
        for medicine in medicines:
            daily_count = medicine.get("dailyCount", 1)
            dosage = medicine.get("dosage", 1)
            period = medicine.get("period", 1)

            times = generate_times(daily_count)
            total_count = daily_count * period * dosage

            doc_ref = db.collection("schedules").document()

            schedule_data = {
                "userId": userId,
                "medicineName": medicine.get("medicineName"),
                "dailyCount": daily_count,
                "times": times,
                "dosage": dosage,
                "timing": medicine.get("timing", "정보 없음"),
                "startDate": startDate,
                "endDate": endDate,
                "period": period,
                "totalCount": total_count,
                "remainingCount": total_count,
                "active": True,
                "source": "OCR",
                "createdAt": datetime.now().isoformat()
            }

            doc_ref.set(schedule_data)

            schedule_data["scheduleId"] = doc_ref.id
            saved_schedules.append(schedule_data)

        return {
            "success": True,
            "message": "OCR 분석 및 복약 일정 저장 완료",
            "filename": file.filename,
            "rawText": raw_text,
            "medicineCount": len(medicines),
            "savedCount": len(saved_schedules),
            "schedules": saved_schedules
        }

    except Exception as e:
        return {
            "success": False,
            "message": "OCR 저장 처리 중 오류가 발생했습니다.",
            "error": str(e)
        }