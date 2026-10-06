from fastapi import APIRouter
from pydantic import BaseModel

from services.interaction_service import (
    check_drug_interactions,
)


router = APIRouter(
    prefix="/interactions",
    tags=["Drug Interaction"],
)


class InteractionCheckRequest(
    BaseModel
):
    userId: str
    itemSeq: str


@router.post("/check")
def check_interaction(
    request: InteractionCheckRequest,
):
    """
    사용자가 현재 복용 중인 약과
    새로 추가하려는 약의

    - 중복 성분
    - 병용 주의
    - 금기사항

    을 검사한다.
    """

    user_id = (
        request.userId.strip()
    )

    item_seq = (
        request.itemSeq.strip()
    )

    if not user_id:
        return {
            "success": False,
            "message":
                "userId가 필요합니다.",
        }

    if not item_seq:
        return {
            "success": False,
            "message":
                "itemSeq가 필요합니다.",
        }

    try:
        return (
            check_drug_interactions(
                user_id=user_id,
                candidate_item_seq=item_seq,
            )
        )

    except Exception as e:
        print(
            "[INTERACTION ERROR]",
            str(e),
        )

        return {
            "success": False,
            "message":
                "약물 상호작용 검사 중 "
                "오류가 발생했습니다.",
            "error":
                str(e),
        }