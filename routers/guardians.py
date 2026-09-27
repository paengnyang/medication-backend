from fastapi import APIRouter, HTTPException

from pydantic import (
    BaseModel,
    Field,
)

from services.guardian_service import (
    create_guardian_code,
    connect_guardian,
    get_guardians_for_ward,
    get_wards_for_guardian,
    get_ward_today_medication_status,
    get_ward_medication_history_by_date,
)


router = APIRouter(
    prefix="/guardians",
    tags=["Guardians"],
)


# ============================================================
# 요청 모델
# ============================================================

class GuardianCodeRequest(
    BaseModel
):
    wardUserId: str = Field(
        ...,
        min_length=1,
    )


class GuardianConnectRequest(
    BaseModel
):
    guardianUserId: str = Field(
        ...,
        min_length=1,
    )

    connectionCode: str = Field(
        ...,
        min_length=6,
        max_length=6,
    )


# ============================================================
# 연결 코드 생성
# ============================================================

@router.post("/code")
def create_code(
    request: GuardianCodeRequest
):

    try:

        result = (
            create_guardian_code(
                request.wardUserId
            )
        )

        return {
            "success":
                True,

            "message":
                "보호자 연결 코드가 생성되었습니다.",

            **result,
        }

    except ValueError as e:

        return {
            "success":
                False,

            "message":
                str(e),
        }


# ============================================================
# 보호자 연결
# ============================================================

@router.post("/connect")
def connect(
    request: GuardianConnectRequest
):

    try:

        result = (
            connect_guardian(
                guardian_user_id=
                    request.guardianUserId,

                connection_code=
                    request.connectionCode,
            )
        )

        return {
            "success":
                True,

            "message":
                "보호자 연결이 완료되었습니다.",

            **result,
        }

    except ValueError as e:

        return {
            "success":
                False,

            "message":
                str(e),
        }


# ============================================================
# 복약 사용자의 보호자 조회
# ============================================================

@router.get(
    "/ward/{ward_user_id}"
)
def get_guardians(
    ward_user_id: str
):

    guardians = (
        get_guardians_for_ward(
            ward_user_id
        )
    )

    return {
        "success":
            True,

        "count":
            len(guardians),

        "guardians":
            guardians,
    }


# ============================================================
# 보호자가 관리하는 사용자 조회
# ============================================================

@router.get(
    "/guardian/{guardian_user_id}"
)
def get_wards(
    guardian_user_id: str
):

    wards = (
        get_wards_for_guardian(
            guardian_user_id
        )
    )

    return {
        "success":
            True,

        "count":
            len(wards),

        "wards":
            wards,
    }
# ============================================================
# 보호자가 사용자의 오늘 복약 현황 조회
# ============================================================

@router.get(
    "/{guardian_user_id}/wards/{ward_user_id}/today"
)
def get_today_medication_status(
    guardian_user_id: str,
    ward_user_id: str,
):

    try:

        result = (
            get_ward_today_medication_status(
                guardian_user_id=
                    guardian_user_id,

                ward_user_id=
                    ward_user_id,
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
            "[GUARDIAN TODAY STATUS ERROR]",
            str(e),
        )

        return {
            "success":
                False,

            "message":
                "복약 현황 조회 중 오류가 발생했습니다.",

            "error":
                str(e),
        }

@router.get(
    "/{guardian_user_id}/wards/{ward_user_id}/medication-history"
)
def get_ward_medication_history(
    guardian_user_id: str,
    ward_user_id: str,
    date: str,
):
    try:
        return (
            get_ward_medication_history_by_date(
                guardian_user_id=
                    guardian_user_id,

                ward_user_id=
                    ward_user_id,

                target_date=
                    date,
            )
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    except Exception as e:

        print(
            "보호자 날짜별 복약 기록 조회 오류:",
            str(e),
        )

        raise HTTPException(
            status_code=500,
            detail=
                "날짜별 복약 기록 조회 중 오류가 발생했습니다.",
        )