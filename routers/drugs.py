import requests
from urllib.parse import urlparse
from fastapi import APIRouter, Query

from services.drug_api_service import (
    search_drug_by_name,
    get_drug_detail_by_item_seq,
)
from fastapi import (
    APIRouter,
    Query,
    HTTPException,
)

from fastapi.responses import Response


router = APIRouter(
    prefix="/drugs",
    tags=["Drugs"],
)


@router.get("/search")
def search_drugs(
    name: str = Query(
        ...,
        min_length=1,
    )
):
    try:

        medicines = (
            search_drug_by_name(
                name
            )
        )

        return {
            "success":
                True,

            "query":
                name,

            "count":
                len(
                    medicines
                ),

            "medicines":
                medicines,
        }

    except Exception as e:

        print(
            "의약품 검색 오류:",
            str(e),
        )

        return {
            "success":
                False,

            "query":
                name,

            "count":
                0,

            "medicines":
                [],

            "error":
                str(e),
        }
@router.get("/image")
def get_drug_image(
    url: str = Query(...)
):
    """
    식약처 의약품 이미지를
    FastAPI가 대신 받아서 Flutter에 전달
    """

    try:
        parsed = urlparse(url)

        # --------------------------------
        # 보안: 식약처 이미지 서버만 허용
        # --------------------------------

        if (
            parsed.scheme != "https"
            or parsed.hostname
            != "nedrug.mfds.go.kr"
        ):
            raise HTTPException(
                status_code=400,
                detail="허용되지 않은 이미지 주소입니다."
            )

        if not parsed.path.startswith(
            "/pbp/cmn/itemImageDownload/"
        ):
            raise HTTPException(
                status_code=400,
                detail="허용되지 않은 이미지 경로입니다."
            )

        # --------------------------------
        # 식약처 이미지 요청
        # --------------------------------

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                    "Mozilla/5.0",
            },
        )

        if response.status_code != 200:
            print(
                "[DRUG IMAGE PROXY] 실패:",
                response.status_code,
                url,
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "식약처 이미지를 "
                    "불러오지 못했습니다."
                ),
            )

        content_type = (
            response.headers.get(
                "Content-Type"
            )
            or "image/jpeg"
        )

        print(
            "[DRUG IMAGE PROXY] 성공:",
            content_type,
            len(response.content),
        )

        return Response(
            content=response.content,
            media_type=content_type,
        )

    except HTTPException:
        raise

    except Exception as e:
        print(
            "[DRUG IMAGE PROXY ERROR]",
            str(e),
        )

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )
# ============================================================
# 의약품 상세 조회
# ============================================================

@router.get("/{item_seq}")
def get_drug_detail(
    item_seq: str
):
    """
    품목기준코드(ITEM_SEQ)를 이용하여
    식약처 의약품 상세정보를 조회한다.

    예:
    GET /drugs/197000053
    """

    try:

        item_seq = item_seq.strip()

        if not item_seq:
            return {
                "success":
                    False,

                "itemSeq":
                    item_seq,

                "drug":
                    None,

                "message":
                    "품목기준코드를 입력해주세요.",
            }

        drug = (
            get_drug_detail_by_item_seq(
                item_seq
            )
        )

        if drug is None:
            return {
                "success":
                    False,

                "itemSeq":
                    item_seq,

                "drug":
                    None,

                "message":
                    "해당 의약품의 상세정보를 찾지 못했습니다.",
            }

        return {
            "success":
                True,

            "itemSeq":
                item_seq,

            "drug":
                drug,
        }

    except Exception as e:

        print(
            "[DRUG DETAIL ERROR]",
            str(e),
        )

        return {
            "success":
                False,

            "itemSeq":
                item_seq,

            "drug":
                None,

            "error":
                str(e),

            "message":
                "의약품 상세정보 조회 중 오류가 발생했습니다.",
        }