import re

from services.interaction_service import (
    _get_drug_detail,
)


def _safe_string(value):
    if value is None:
        return ""

    return str(value).strip()


def _normalize_detail_result(
    detail_result,
    item_seq,
):
    """
    _get_drug_detail() 반환 형식이

    {
        "success": True,
        "drug": {...}
    }

    또는

    {
        "itemName": "...",
        "raw": {...}
    }

    둘 중 어느 형태여도 처리한다.
    """

    if (
        not isinstance(
            detail_result,
            dict,
        )
        or not detail_result
    ):
        return None, {
            "success": False,
            "itemSeq": item_seq,
            "error":
                "약 상세정보 조회 실패",
        }

    if (
        detail_result.get(
            "success"
        )
        is False
    ):
        return None, {
            "success": False,
            "itemSeq": item_seq,
            "error":
                detail_result.get(
                    "error"
                )
                or "약 상세정보 조회 실패",
        }

    if isinstance(
        detail_result.get(
            "drug"
        ),
        dict,
    ):
        drug = (
            detail_result[
                "drug"
            ]
        )

    else:
        drug = detail_result

    return drug, None


def _split_pack_units(
    pack_unit,
):
    """
    예:

    블리스터포장 - 10정/블리스터,
    병포장 - 60정/병

    을 각각의 포장 설명으로 분리한다.
    """

    text = _safe_string(
        pack_unit
    )

    if not text:
        return []

    text = (
        text
        .replace("\r", " ")
        .replace("\n", " ")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    parts = re.split(
        r"\s*[,;]\s*",
        text,
    )

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


def _detect_package_type(
    text,
):
    """
    포장 형태를 간단히 분류한다.
    """

    if "블리스터" in text:
        return "blister"

    if (
        "PTP" in text.upper()
        or "피티피" in text
    ):
        return "ptp"

    if "병" in text:
        return "bottle"

    if "포" in text:
        return "sachet"

    if "앰플" in text:
        return "ampoule"

    if "바이알" in text:
        return "vial"

    if "튜브" in text:
        return "tube"

    return "other"


def _parse_package_option(
    text,
):
    """
    포장 문장에서
    개수 + 단위를 추출한다.

    예:
    블리스터포장 - 10정/블리스터
    -> 10, 정

    병포장 - 60정/병
    -> 60, 정
    """

    pattern = re.compile(
        r"(\d+(?:\.\d+)?)"
        r"\s*"
        r"(정|캡슐|캅셀|포|"
        r"병|앰플|바이알|"
        r"튜브|개|매|"
        r"mL|ml|밀리리터)",
        re.IGNORECASE,
    )

    match = pattern.search(
        text
    )

    if not match:
        return {
            "rawText": text,
            "count": None,
            "unit": "",
            "packageType":
                _detect_package_type(
                    text
                ),
            "container": "",
        }

    count_text = (
        match.group(1)
    )

    unit = (
        match.group(2)
    )

    try:
        count_value = float(
            count_text
        )

        if (
            count_value
            .is_integer()
        ):
            count_value = int(
                count_value
            )

    except ValueError:
        count_value = None

    container = ""

    slash_match = re.search(
        r"/\s*([^,\s]+)",
        text,
    )

    if slash_match:
        container = (
            slash_match
            .group(1)
            .strip()
        )

    return {
        "rawText": text,
        "count": count_value,
        "unit": unit,
        "packageType":
            _detect_package_type(
                text
            ),
        "container":
            container,
    }


def _parse_pack_unit(
    pack_unit,
):
    parts = (
        _split_pack_units(
            pack_unit
        )
    )

    options = []

    seen = set()

    for part in parts:
        option = (
            _parse_package_option(
                part
            )
        )

        key = (
            option.get(
                "count"
            ),
            option.get(
                "unit"
            ),
            option.get(
                "packageType"
            ),
            option.get(
                "rawText"
            ),
        )

        if key in seen:
            continue

        seen.add(key)

        options.append(
            option
        )

    return options


def get_package_info(
    item_seq,
):
    detail_result = (
        _get_drug_detail(
            item_seq
        )
    )

    drug, error = (
        _normalize_detail_result(
            detail_result,
            item_seq,
        )
    )

    if error:
        return error

    raw = (
        drug.get("raw")
        if isinstance(
            drug.get("raw"),
            dict,
        )
        else {}
    )

    medicine_name = (
        _safe_string(
            drug.get(
                "itemName"
            )
            or raw.get(
                "ITEM_NAME"
            )
        )
    )

    pack_unit = (
        _safe_string(
            drug.get(
                "packUnit"
            )
            or raw.get(
                "PACK_UNIT"
            )
        )
    )

    package_options = (
        _parse_pack_unit(
            pack_unit
        )
    )

    valid_options = [
        option
        for option
        in package_options
        if (
            option.get(
                "count"
            )
            is not None
        )
    ]

    # 판매 포장이 딱 하나일 때만
    # 자동 기본값으로 사용한다.
    default_count = None
    default_unit = ""

    if len(valid_options) == 1:
        default_count = (
            valid_options[0]
            .get("count")
        )

        default_unit = (
            valid_options[0]
            .get("unit")
            or ""
        )

    return {
        "success": True,

        "itemSeq":
            item_seq,

        "medicineName":
            medicine_name,

        "packUnitRaw":
            pack_unit,

        "packageOptionCount":
            len(
                valid_options
            ),

        "packageOptions":
            valid_options,

        "requiresSelection":
            len(
                valid_options
            ) > 1,

        "defaultPackageCount":
            default_count,

        "defaultPackageUnit":
            default_unit,

        "message":
            (
                "여러 포장 단위가 있어 "
                "사용자가 구매한 포장을 "
                "선택해야 합니다."
                if len(
                    valid_options
                ) > 1
                else
                "포장 단위 조회 완료"
            ),
    }