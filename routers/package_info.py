from fastapi import (
    APIRouter,
)

from services.package_info_service import (
    get_package_info,
)


router = APIRouter(
    prefix="/package-info",
    tags=["Package Info"],
)


@router.get("/{item_seq}")
def package_info(
    item_seq: str,
):
    return get_package_info(
        item_seq
    )