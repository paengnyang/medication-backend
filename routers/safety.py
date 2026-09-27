from typing import List

from fastapi import APIRouter

from pydantic import (
    BaseModel,
    Field,
)

from services.safety_service import (
    check_medicine_safety,
    check_new_medicine_with_current,
)


router = APIRouter(
    prefix="/safety",
    tags=["Safety"],
)


# ============================================================
# 요청 Body
# ============================================================

class SafetyCheckRequest(
    BaseModel
):
    userId: str = Field(
        ...,
        min_length=1,
    )

    itemSeqs: List[str] = Field(
        ...,
        min_length=1,
    )

class CurrentMedicationSafetyRequest(
    BaseModel
):
    userId: str = Field(
        ...,
        min_length=1,
    )

    newItemSeq: str = Field(
        ...,
        min_length=1,
    )

# ============================================================
# 알레르기 + 중복 성분 검사
# ============================================================

@router.post("/check")
def check_safety(
    request: SafetyCheckRequest
):
    try:

        result = (
            check_medicine_safety(
                user_id=
                    request.userId,

                item_seqs=
                    request.itemSeqs,
            )
        )

        return {
            "success":
                True,

            **result,
        }

    except ValueError as e:

        return {
            "success":
                False,

            "message":
                str(e),
        }

    except Exception as e:

        print(
            "[SAFETY CHECK ERROR]",
            str(e),
        )

        return {
            "success":
                False,

            "message":
                "의약품 안전성 검사 중 오류가 발생했습니다.",

            "error":
                str(e),
        }
# ============================================================
# 새 약 + 현재 복용 중인 약 자동 안전성 검사
# ============================================================

@router.post(
    "/check-with-current-medications"
)
def check_with_current_medications(
    request:
        CurrentMedicationSafetyRequest
):
    try:

        result = (
            check_new_medicine_with_current(
                user_id=
                    request.userId,

                new_item_seq=
                    request.newItemSeq,
            )
        )

        return {
            "success":
                True,

            **result,
        }

    except ValueError as e:

        return {
            "success":
                False,

            "message":
                str(e),
        }

    except Exception as e:

        print(
            "[CURRENT MEDICATION SAFETY ERROR]",
            str(e),
        )

        return {
            "success":
                False,

            "message":
                (
                    "현재 복용 중인 의약품과의 "
                    "안전성 검사 중 오류가 발생했습니다."
                ),

            "error":
                str(e),
        }  