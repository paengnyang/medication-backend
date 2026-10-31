import os
import requests

from dotenv import load_dotenv


# ============================================================
# .env 로드
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

ENV_PATH = os.path.join(
    BASE_DIR,
    ".env"
)

load_dotenv(
    dotenv_path=ENV_PATH
)


MFDS_SERVICE_KEY = os.getenv(
    "MFDS_SERVICE_KEY"
)

MFDS_DRUG_API_URL = os.getenv(
    "MFDS_DRUG_API_URL"
)

MFDS_DRUG_DETAIL_API_URL = os.getenv(
    "MFDS_DRUG_DETAIL_API_URL"
)

print(
    "[MFDS] ENV PATH:",
    ENV_PATH
)

print(
    "[MFDS] KEY EXISTS:",
    bool(MFDS_SERVICE_KEY)
)

print(
    "[MFDS] API URL:",
    MFDS_DRUG_API_URL
)


# ============================================================
# 의약품명 검색
# ============================================================

def search_drug_by_name(
    medicine_name: str
):
    """
    식약처 의약품 제품 허가 목록에서
    품목명으로 검색한다.
    """

    if not MFDS_SERVICE_KEY:
        raise RuntimeError(
            "MFDS_SERVICE_KEY가 설정되지 않았습니다."
        )

    if not MFDS_DRUG_API_URL:
        raise RuntimeError(
            "MFDS_DRUG_API_URL이 설정되지 않았습니다."
        )

    medicine_name = medicine_name.strip()

    if not medicine_name:
        return []

    params = {
        "serviceKey":
            MFDS_SERVICE_KEY,

        "pageNo":
            "1",

        "numOfRows":
            "20",

        "type":
            "json",

        "item_name":
            medicine_name,
    }

    print(
        "[MFDS] 검색:",
        medicine_name
    )

    response = requests.get(
        MFDS_DRUG_API_URL,
        params=params,
        timeout=25,
    )

    print(
        "[MFDS] STATUS:",
        response.status_code
    )

    print(
        "[MFDS] RESPONSE:",
        response.text[:1000]
    )

    response.raise_for_status()

    try:
        data = response.json()

    except ValueError:
        raise RuntimeError(
            "식약처 API가 JSON이 아닌 응답을 반환했습니다."
        )

    # ========================================================
    # 응답 구조 처리
    # ========================================================

    body = (
        data
        .get("body")
    )

    # 일부 공공 API는 response.body 구조 사용
    if body is None:
        body = (
            data
            .get("response", {})
            .get("body", {})
        )

    if not isinstance(
        body,
        dict
    ):
        return []

    items = body.get(
        "items",
        []
    )

    # 구조:
    # items: [...]
    if isinstance(
        items,
        list
    ):
        raw_items = items

    # 구조:
    # items: {item: [...]}
    elif isinstance(
        items,
        dict
    ):
        raw_items = items.get(
            "item",
            []
        )

    else:
        raw_items = []

    if isinstance(
        raw_items,
        dict
    ):
        raw_items = [
            raw_items
        ]

    results = []

    for drug in raw_items:

        if not isinstance(
            drug,
            dict
        ):
            continue

        results.append({
            "itemName":
                drug.get("ITEM_NAME")
                or drug.get("item_name")
                or drug.get("ITEM_NAME_ENG"),

            "itemSeq":
                drug.get("ITEM_SEQ")
                or drug.get("item_seq"),

            "entpName":
                drug.get("ENTP_NAME")
                or drug.get("entp_name"),

            "mainIngredient":
                drug.get("MAIN_ITEM_INGR")
                or drug.get("main_item_ingr"),

            "category":
                drug.get("SPCLTY_PBLC"),

            "productType":
                 drug.get("PRDUCT_TYPE"),

            "permitDate":
                drug.get("ITEM_PERMIT_DATE"),

            "cancelName":
                drug.get("CANCEL_NAME"),

            "imageUrl":
                 drug.get("BIG_PRDT_IMG_URL"),

            "itemEngName":
                 drug.get("ITEM_ENG_NAME"),

            "raw":
                drug,
        })

    return results
def get_drug_detail_by_item_seq(
    item_seq: str
):
    """
    식약처 의약품 제품 허가 상세정보 API에서
    품목기준코드(ITEM_SEQ)로 의약품 상세정보를 조회한다.
    """

    if not MFDS_SERVICE_KEY:
        raise RuntimeError(
            "MFDS_SERVICE_KEY가 설정되지 않았습니다."
        )

    if not MFDS_DRUG_DETAIL_API_URL:
        raise RuntimeError(
            "MFDS_DRUG_DETAIL_API_URL이 설정되지 않았습니다."
        )

    item_seq = str(
        item_seq
    ).strip()

    if not item_seq:
        return None

    params = {
        "serviceKey":
            MFDS_SERVICE_KEY,

        "pageNo":
            "1",

        "numOfRows":
            "10",

        "type":
            "json",

        # 품목기준코드
        "item_seq":
            item_seq,
    }

    print(
        "[MFDS DETAIL] 품목기준코드:",
        item_seq
    )

    response = requests.get(
        MFDS_DRUG_DETAIL_API_URL,
        params=params,
        timeout=25,
    )

    print(
        "[MFDS DETAIL] STATUS:",
        response.status_code
    )

    print(
        "[MFDS DETAIL] RESPONSE:",
        response.text[:1500]
    )

    response.raise_for_status()

    # ========================================================
    # JSON 변환
    # ========================================================

    try:
        data = response.json()

    except ValueError:
        raise RuntimeError(
            "식약처 상세정보 API가 "
            "JSON이 아닌 응답을 반환했습니다."
        )

    # ========================================================
    # body 찾기
    #
    # API마다
    # data["body"]
    #
    # 또는
    #
    # data["response"]["body"]
    #
    # 형태가 조금씩 달라질 수 있어서 둘 다 처리
    # ========================================================

    body = data.get(
        "body"
    )

    if body is None:
        body = (
            data
            .get(
                "response",
                {}
            )
            .get(
                "body",
                {}
            )
        )

    if not isinstance(
        body,
        dict
    ):
        return None

    items = body.get(
        "items",
        []
    )

    # ========================================================
    # items 구조 정리
    # ========================================================

    if isinstance(
        items,
        list
    ):
        raw_items = items

    elif isinstance(
        items,
        dict
    ):
        # 어떤 API는
        # items: {"item": [...]}
        raw_items = items.get(
            "item",
            []
        )

        # item 없이 items 자체가 약 정보인 경우
        if not raw_items:
            raw_items = [
                items
            ]

    else:
        raw_items = []

    if isinstance(
        raw_items,
        dict
    ):
        raw_items = [
            raw_items
        ]

    if not raw_items:
        return None

    # ========================================================
    # ITEM_SEQ가 정확히 일치하는 약 찾기
    # ========================================================

    selected_drug = None

    for drug in raw_items:

        if not isinstance(
            drug,
            dict
        ):
            continue

        current_seq = str(
            drug.get(
                "ITEM_SEQ",
                ""
            )
        ).strip()

        if (
            current_seq
            == item_seq
        ):
            selected_drug = drug
            break

    # 정확히 못 찾았지만 결과가 1개라면 사용
    if (
        selected_drug is None
        and len(raw_items) == 1
        and isinstance(
            raw_items[0],
            dict
        )
    ):
        selected_drug = (
            raw_items[0]
        )

    if selected_drug is None:
        return None

    drug = selected_drug

    # ========================================================
    # Flutter에서 사용하기 편한 형식으로 변환
    # ========================================================

    result = {
        # -----------------------------
        # 기본 정보
        # -----------------------------

        "itemSeq":
            drug.get(
                "ITEM_SEQ"
            ),

        "itemName":
            drug.get(
                "ITEM_NAME"
            ),

        "itemEngName":
            drug.get(
                "ITEM_ENG_NAME"
            ),

        "entpName":
            drug.get(
                "ENTP_NAME"
            ),

        # -----------------------------
        # 의약품 분류
        # -----------------------------

        "category":
            drug.get(
                "SPCLTY_PBLC"
            ),

        "productType":
            drug.get(
                "PRDUCT_TYPE"
            ),

        "permitDate":
            drug.get(
                "ITEM_PERMIT_DATE"
            ),

        # -----------------------------
        # 성분
        # -----------------------------

        "mainIngredient":
            (
                drug.get(
                    "ITEM_INGR_NAME"
                )
                or drug.get(
                    "MAIN_ITEM_INGR"
                )
            ),

        "ingredientCount":
            drug.get(
                "ITEM_INGR_CNT"
            ),

        # -----------------------------
        # 효능 / 용법 / 주의사항
        # -----------------------------

        "efficacy":
            drug.get(
                "EE_DOC_DATA"
            ),

        "usage":
            drug.get(
                "UD_DOC_DATA"
            ),

        "precautions":
            drug.get(
                "NB_DOC_DATA"
            ),

        # -----------------------------
        # 보관
        # -----------------------------

        "storageMethod":
            drug.get(
                "STORAGE_METHOD"
            ),

        "validTerm":
            drug.get(
                "VALID_TERM"
            ),

        # -----------------------------
        # 이미지
        # -----------------------------

        "imageUrl":
            drug.get(
                "BIG_PRDT_IMG_URL"
            ),

        # -----------------------------
        # 허가 상태
        # -----------------------------

        "cancelName":
            drug.get(
                "CANCEL_NAME"
            ),

        "cancelDate":
            drug.get(
                "CANCEL_DATE"
            ),

        # 식약처 전체 데이터도 보존
        "raw":
            drug,
    }

    return result