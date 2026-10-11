from fastapi import (
    APIRouter,
)

from services.dose_info_service import (
    get_dose_info,
)


router = APIRouter(
    prefix="/dose-info",
    tags=["Dose Info"],
)


@router.get("/{item_seq}")
def dose_info(
    item_seq: str,
):
    return get_dose_info(
        item_seq
    )