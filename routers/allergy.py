from fastapi import (
    APIRouter,
)

from pydantic import (
    BaseModel,
)

from services.allergy_service import (
    check_allergy,
)


router = APIRouter(
    prefix="/allergy",
    tags=["Allergy"],
)


class AllergyCheckRequest(
    BaseModel
):
    userId: str
    itemSeq: str


@router.post("/check")
def allergy_check(
    request:
        AllergyCheckRequest,
):
    return check_allergy(
        user_id=request.userId,
        item_seq=request.itemSeq,
    )