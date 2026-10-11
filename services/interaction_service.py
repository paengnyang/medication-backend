import html
import json
import os
import re
from datetime import date
from urllib.parse import quote
from urllib.parse import (
    quote,
    urlencode,
    unquote,
)
from urllib.request import urlopen

from dotenv import load_dotenv

from firebase_config import db

load_dotenv()
# ============================================================
# 설정
# ============================================================

# 현재 백엔드의 약 상세 API를 재사용한다.
#
# 로컬 실행:
# http://127.0.0.1:8000/drugs/{itemSeq}
#
# 나중에 배포할 경우 .env에
# BACKEND_INTERNAL_URL=...
# 를 넣으면 된다.
BACKEND_INTERNAL_URL = os.getenv(
    "BACKEND_INTERNAL_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

# ============================================================
# 식약처 DUR 병용금기 API
# ============================================================

DUR_API_URL = (
    "https://apis.data.go.kr/"
    "1471000/"
    "DURPrdlstInfoService03/"
    "getUsjntTabooInfoList03"
)

DUR_BASE_URL = (
    "https://apis.data.go.kr/"
    "1471000/"
    "DURPrdlstInfoService03"
)

DUR_AGE_ENDPOINT = (
    "getSpcifyAgrdeTabooInfoList03"
)
DUR_PREGNANCY_ENDPOINT = (
    "getPwnmTabooInfoList03"
)
DUR_DOSAGE_ENDPOINT = (
    "getCpctyAtentInfoList03"
)
DUR_EFFICACY_ENDPOINT = (
    "getEfcyDplctInfoList03"
)
# 기존 식약처 API 키를 그대로 사용할 수 있게
# 여러 이름을 fallback으로 확인한다.
DUR_API_KEY = (
    os.getenv("MFDS_DUR_API_KEY")
    or os.getenv("MFDS_API_KEY")
    or os.getenv("MFDS_SERVICE_KEY")
    or os.getenv("DATA_GO_KR_SERVICE_KEY")
    or os.getenv("SERVICE_KEY")
    or ""
).strip()
_dur_dataset_cache = {}
# Encoding 키를 저장해둔 경우
# urlencode에서 이중 인코딩되지 않도록 한 번 decode
DUR_API_KEY = unquote(
    DUR_API_KEY
)
# ============================================================
# 공통 문자열 처리
# ============================================================

def _safe_string(value):
    if value is None:
        return ""

    return str(value).strip()


def _normalize_for_match(text):
    """
    성분명/약 이름 비교용 문자열 정규화.

    예:
    아세트아미노펜 500mg
    → 아세트아미노펜500mg
    """

    text = _safe_string(text).lower()

    text = re.sub(
        r"[\s\-\_\(\)\[\]\{\},./·ㆍ:;]",
        "",
        text,
    )

    return text


def _normalize_markup(text):
    """
    식약처 XML 문서가
    \\<ARTICLE ...\\>
    형태로 들어오는 경우까지 처리한다.
    """

    text = _safe_string(text)

    text = (
        text
        .replace(r"\<", "<")
        .replace(r"\>", ">")
        .replace(r"\"", '"')
    )

    return text


def _clean_document_text(text):
    """
    XML/HTML 태그 제거 후 사람이 읽을 수 있는 텍스트로 변환.
    """

    text = _normalize_markup(text)

    # CDATA
    text = re.sub(
        r"<!\[CDATA\[(.*?)\]\]>",
        r"\1",
        text,
        flags=re.DOTALL,
    )

    # XML / HTML 태그 제거
    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# 약 상세정보 조회
# ============================================================

def _get_drug_detail(item_seq):
    """
    이미 구현된
    GET /drugs/{itemSeq}
    를 백엔드 내부에서 재사용한다.
    """

    item_seq = _safe_string(item_seq)

    if not item_seq:
        return None

    url = (
        f"{BACKEND_INTERNAL_URL}"
        f"/drugs/{quote(item_seq)}"
    )

    try:
        with urlopen(
            url,
            timeout=15,
        ) as response:

            body = response.read().decode(
                "utf-8"
            )

        result = json.loads(body)

        if result.get("success") is not True:
            return None

        drug = result.get("drug")

        if not isinstance(drug, dict):
            return None

        return drug

    except Exception as e:
        print(
            "[INTERACTION]"
            " 약 상세정보 조회 실패:",
            item_seq,
            str(e),
        )

        return None

def _search_drug_detail_by_name(
    medicine_name
):
    """
    기존 Firestore 약에 itemSeq가 없는 경우
    약 이름으로 검색해서 itemSeq를 찾은 뒤
    상세정보까지 조회한다.
    """

    medicine_name = _safe_string(
        medicine_name
    )

    if not medicine_name:
        return None

    url = (
        f"{BACKEND_INTERNAL_URL}"
        f"/drugs/search"
        f"?name={quote(medicine_name)}"
    )

    try:
        with urlopen(
            url,
            timeout=15,
        ) as response:

            body = (
                response
                .read()
                .decode("utf-8")
            )

        result = json.loads(body)

        medicines = result.get(
            "medicines"
        )

        if not isinstance(
            medicines,
            list,
        ):
            return None

        target = (
            medicine_name
            .replace(" ", "")
            .lower()
        )

        matched = None

        # 정확한 약 이름 우선
        for drug in medicines:

            if not isinstance(
                drug,
                dict,
            ):
                continue

            item_name = _safe_string(
                drug.get("itemName")
            )

            normalized_name = (
                item_name
                .replace(" ", "")
                .lower()
            )

            if normalized_name == target:
                matched = drug
                break

        # 결과가 하나뿐이면 사용
        if (
            matched is None
            and len(medicines) == 1
            and isinstance(
                medicines[0],
                dict,
            )
        ):
            matched = medicines[0]

        if matched is None:
            return None

        item_seq = _safe_string(
            matched.get("itemSeq")
        )

        if not item_seq:

            raw = matched.get("raw")

            if isinstance(raw, dict):
                item_seq = _safe_string(
                    raw.get("ITEM_SEQ")
                )

        if not item_seq:
            return None

        return _get_drug_detail(
            item_seq
        )

    except Exception as e:
        print(
            "[INTERACTION]"
            " 약 이름 검색 실패:",
            medicine_name,
            str(e),
        )

        return None

# ============================================================
# DUR API 공통 처리
# ============================================================

def _extract_dur_items(data):
    """
    공공데이터포털 응답 구조가

    {
        "body": {...}
    }

    또는

    {
        "response": {
            "body": {...}
        }
    }

    둘 중 어느 형식이어도 처리한다.
    """

    if not isinstance(data, dict):
        return []

    # --------------------------------
    # response.body 구조
    # --------------------------------

    response = data.get(
        "response"
    )

    if isinstance(response, dict):
        body = response.get(
            "body"
        )
    else:
        body = data.get(
            "body"
        )

    if not isinstance(body, dict):
        return []

    items = body.get(
        "items"
    )

    if items is None:
        return []

    # items 자체가 실제 배열인 경우
    if isinstance(items, list):
        return [
            item
            for item in items
            if isinstance(item, dict)
        ]

    # items가 단일 객체인 경우
    if isinstance(items, dict):

        # 일부 공공 API:
        #
        # "items": {
        #     "item": [...]
        # }

        nested_item = items.get(
            "item"
        )

        if isinstance(
            nested_item,
            list,
        ):
            return [
                item
                for item
                in nested_item
                if isinstance(
                    item,
                    dict,
                )
            ]

        if isinstance(
            nested_item,
            dict,
        ):
            return [
                nested_item
            ]

        # items 자체가 DUR 데이터 1건
        return [
            items
        ]

    return []

def _extract_dur_total_count(
    data
):
    if not isinstance(
        data,
        dict,
    ):
        return 0

    response = data.get(
        "response"
    )

    if isinstance(
        response,
        dict,
    ):
        body = response.get(
            "body"
        )
    else:
        body = data.get(
            "body"
        )

    if not isinstance(
        body,
        dict,
    ):
        return 0

    try:
        return int(
            body.get(
                "totalCount",
                0,
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0

def _load_dur_dataset(
    endpoint_name
):
    """
    DUR 데이터를 전부 받아 캐시에 저장한다.

    같은 서버 실행 중에는
    두 번째부터 API를 다시 호출하지 않는다.
    """

    cached = (
        _dur_dataset_cache.get(
            endpoint_name
        )
    )

    if cached is not None:
        return {
            "success": True,
            "items": cached,
            "error": None,
        }

    if not DUR_API_KEY:
        return {
            "success": False,
            "items": [],
            "error":
                "DUR API 인증키가 없습니다.",
        }

    page = 1
    page_size = 1000

    all_items = []

    try:
        while True:

            params = {
                "serviceKey":
                    DUR_API_KEY,

                "type":
                    "json",

                "pageNo":
                    page,

                "numOfRows":
                    page_size,
            }

            url = (
                f"{DUR_BASE_URL}/"
                f"{endpoint_name}"
                f"?{urlencode(params)}"
            )

            print(
                "[DUR DATASET]",
                endpoint_name,
                "page:",
                page,
            )

            with urlopen(
                url,
                timeout=30,
            ) as response:

                body = (
                    response
                    .read()
                    .decode(
                        "utf-8"
                    )
                )

            data = json.loads(
                body
            )

            items = (
                _extract_dur_items(
                    data
                )
            )

            total_count = (
                _extract_dur_total_count(
                    data
                )
            )

            all_items.extend(
                items
            )

            print(
                "[DUR DATASET]",
                len(all_items),
                "/",
                total_count,
            )

            if not items:
                break

            if (
                total_count > 0
                and len(all_items)
                >= total_count
            ):
                break

            if len(items) < page_size:
                break

            page += 1

            # 비정상 응답으로 무한루프 방지
            if page > 100:
                break

        _dur_dataset_cache[
            endpoint_name
        ] = all_items

        return {
            "success": True,
            "items": all_items,
            "error": None,
        }

    except Exception as e:

        print(
            "[DUR DATASET ERROR]",
            endpoint_name,
            str(e),
        )

        return {
            "success": False,
            "items": [],
            "error": str(e),
        }

def _dur_item_matches_drug(
    item,
    medicine_name,
    ingredients,
):
    """
    DUR 데이터가 현재 약에 해당하는지 확인.
    """

    target_name = (
        _normalize_for_match(
            medicine_name
        )
    )

    dur_item_name = (
        _normalize_for_match(
            item.get(
                "ITEM_NAME"
            )
        )
    )

    # 제품명이 정확히 같은 경우
    if (
        target_name
        and dur_item_name
        and target_name
        == dur_item_name
    ):
        return True

    # --------------------------------
    # 성분명으로 한 번 더 확인
    # --------------------------------

    dur_ingredient = (
        item.get("INGR_NAME")
        or item.get(
            "INGR_KOR_NAME"
        )
        or ""
    )

    dur_ingredient = (
        _normalize_for_match(
            dur_ingredient
        )
    )

    if not dur_ingredient:
        return False

    for ingredient in ingredients:

        normalized = (
            _normalize_for_match(
                ingredient
            )
        )

        if not normalized:
            continue

        if (
            normalized
            == dur_ingredient
        ):
            return True

    return False

def _get_user_age(
    user_id
):
    """
    Firestore users/{userId}의
    생년월일로 만 나이를 계산.
    """

    try:
        user_doc = (
            db.collection(
                "users"
            )
            .document(
                user_id
            )
            .get()
        )
        

        if not user_doc.exists:
            return None

        user = (
            user_doc.to_dict()
            or {}
        )

        year = int(
            user.get(
                "birthYear"
            )
        )

        month = int(
            user.get(
                "birthMonth"
            )
        )

        day = int(
            user.get(
                "birthDay"
            )
        )

        birth_date = date(
            year,
            month,
            day,
        )

        today = date.today()

        age = (
            today.year
            - birth_date.year
            - (
                (
                    today.month,
                    today.day,
                )
                <
                (
                    birth_date.month,
                    birth_date.day,
                )
            )
        )

        # 개월 수도 계산
        months = (
            (
                today.year
                - birth_date.year
            )
            * 12
            + today.month
            - birth_date.month
        )

        if (
            today.day
            < birth_date.day
        ):
            months -= 1

        return {
            "age":
                age,

            "ageMonths":
                max(
                    months,
                    0,
                ),

            "birthDate":
                birth_date.isoformat(),
        }

    except Exception as e:

        print(
            "[DUR AGE]"
            " 사용자 나이 계산 실패:",
            str(e),
        )

        return None

def _get_user_pregnancy_status(
    user_id
):
    """
    Firestore users/{userId}의
    임신 여부를 읽는다.

    반환:
        True  -> 임신
        False -> 비임신
        None  -> 정보 없음
    """

    try:
        user_doc = (
            db.collection(
                "users"
            )
            .document(
                user_id
            )
            .get()
        )

        if not user_doc.exists:
            return {
                "pregnant": None,
                "gender": "",
                "rawValue": None,
            }

        user = (
            user_doc.to_dict()
            or {}
        )

        pregnancy = user.get(
            "pregnancy"
        )

        gender = _safe_string(
            user.get(
                "gender"
            )
        )

        # bool로 저장된 경우
        if isinstance(
            pregnancy,
            bool,
        ):
            return {
                "pregnant":
                    pregnancy,

                "gender":
                    gender,

                "rawValue":
                    pregnancy,
            }

        value = (
            _safe_string(
                pregnancy
            )
            .lower()
        )

        # 현재 Flutter에서 사용하는 O
        if value in {
            "o",
            "yes",
            "y",
            "true",
            "1",
            "임신",
        }:
            return {
                "pregnant": True,
                "gender": gender,
                "rawValue": pregnancy,
            }

        # 현재 Flutter에서 사용하는 X
        if value in {
            "x",
            "no",
            "n",
            "false",
            "0",
            "비임신",
        }:
            return {
                "pregnant": False,
                "gender": gender,
                "rawValue": pregnancy,
            }

        return {
            "pregnant": None,
            "gender": gender,
            "rawValue": pregnancy,
        }

    except Exception as e:

        print(
            "[DUR PREGNANCY]"
            " 사용자 정보 조회 실패:",
            str(e),
        )

        return {
            "pregnant": None,
            "gender": "",
            "rawValue": None,
        }

def _check_pregnancy_dur(
    user_id,
    medicine_name,
    ingredients,
):
    """
    사용자 임신 여부와
    식약처 DUR 임부금기 데이터를 비교한다.
    """

    user_status = (
        _get_user_pregnancy_status(
            user_id
        )
    )

    pregnant = (
        user_status.get(
            "pregnant"
        )
    )

    gender = (
        user_status.get(
            "gender",
            "",
        )
    )

    # ========================================
    # 임신 정보 자체가 없는 경우
    # ========================================

    if pregnant is None:
        return {
            "success": True,

            "checked": False,

            "pregnant": None,

            "gender": gender,

            "matchedRuleCount": 0,

            "warnings": [],

            "error": None,

            "message":
                "사용자의 임신 여부 정보가 "
                "없어 임부금기 검사를 "
                "적용하지 않았습니다.",
        }

    # ========================================
    # 임신하지 않은 경우
    # DUR 전체 데이터를 불러올 필요도 없음
    # ========================================

    if pregnant is False:
        return {
            "success": True,

            "checked": True,

            "pregnant": False,

            "gender": gender,

            "matchedRuleCount": 0,

            "warnings": [],

            "error": None,

            "message":
                "현재 사용자 정보상 "
                "임신 상태가 아니므로 "
                "임부금기 경고를 "
                "적용하지 않았습니다.",
        }

    # ========================================
    # 임신 상태인 경우만 DUR 조회
    # ========================================

    dataset = (
        _load_dur_dataset(
            DUR_PREGNANCY_ENDPOINT
        )
    )

    if not dataset.get(
        "success"
    ):
        return {
            "success": False,

            "checked": False,

            "pregnant": True,

            "gender": gender,

            "matchedRuleCount": 0,

            "warnings": [],

            "error":
                dataset.get(
                    "error"
                ),

            "message":
                "DUR 임부금기 데이터를 "
                "조회하지 못했습니다.",
        }

    matched_items = []

    for item in dataset.get(
        "items",
        [],
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue

        if _dur_item_matches_drug(
            item,
            medicine_name,
            ingredients,
        ):
            matched_items.append(
                item
            )

    warnings = []

    for item in matched_items:

        ingredient = (
            _safe_string(
                item.get(
                    "INGR_NAME"
                )
            )
            or
            _safe_string(
                item.get(
                    "INGR_KOR_NAME"
                )
            )
            or
            _safe_string(
                item.get(
                    "MAIN_INGR"
                )
            )
        )

        prohibition = (
            _safe_string(
                item.get(
                    "PROHBT_CONTENT"
                )
            )
        )

        remark = (
            _safe_string(
                item.get(
                    "REMARK"
                )
            )
        )

        warnings.append(
            {
                "type":
                    "dur_pregnancy_contraindication",

                "severity":
                    "danger",

                "medicineName":
                    medicine_name,

                "ingredient":
                    ingredient,

                "ruleType":
                    (
                        _safe_string(
                            item.get(
                                "TYPE_NAME"
                            )
                        )
                        or "임부금기"
                    ),

                "prohibition":
                    prohibition,

                "remark":
                    remark,

                "notificationDate":
                    _safe_string(
                        item.get(
                            "NOTIFICATION_DATE"
                        )
                    ),

                "message":
                    (
                        f"{medicine_name}은 "
                        "식약처 DUR 임부금기 "
                        "정보에 해당합니다."
                    ),
            }
        )

    return {
        "success": True,

        "checked": True,

        "pregnant": True,

        "gender": gender,

        "matchedRuleCount":
            len(
                matched_items
            ),

        "warnings":
            warnings,

        "error": None,

        "message":
            (
                "임부금기 정보가 "
                "확인되었습니다."
                if warnings
                else
                "해당 의약품의 DUR "
                "임부금기 정보가 "
                "확인되지 않았습니다."
            ),
    }

def _check_dosage_dur(
    medicine_name,
    ingredients,
):
    """
    식약처 DUR 용량주의 데이터에서
    현재 약이 용량주의 대상인지 확인한다.

    현재 단계에서는 실제 복용량 초과 여부가 아니라
    'DUR 용량주의 대상 품목인지'를 판정한다.
    """

    dataset = (
        _load_dur_dataset(
            DUR_DOSAGE_ENDPOINT
        )
    )

    if not dataset.get(
        "success"
    ):
        return {
            "success": False,
            "matchedRuleCount": 0,
            "warnings": [],
            "error":
                dataset.get(
                    "error"
                ),
        }

    matched_items = []

    for item in dataset.get(
        "items",
        [],
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue

        if _dur_item_matches_drug(
            item,
            medicine_name,
            ingredients,
        ):
            matched_items.append(
                item
            )

    warnings = []

    for item in matched_items:

        ingredient = (
            _safe_string(
                item.get(
                    "INGR_NAME"
                )
            )
            or
            _safe_string(
                item.get(
                    "INGR_KOR_NAME"
                )
            )
            or
            _safe_string(
                item.get(
                    "MAIN_INGR"
                )
            )
        )

        prohibition = (
            _safe_string(
                item.get(
                    "PROHBT_CONTENT"
                )
            )
        )

        remark = (
            _safe_string(
                item.get(
                    "REMARK"
                )
            )
        )

        warnings.append(
            {
                "type":
                    "dur_dosage_caution",

                # 아직 실제 투여량과 비교한 것은 아니므로
                # danger가 아니라 warning
                "severity":
                    "warning",

                "medicineName":
                    medicine_name,

                "itemName":
                    _safe_string(
                        item.get(
                            "ITEM_NAME"
                        )
                    ),

                "ingredient":
                    ingredient,

                "ruleType":
                    (
                        _safe_string(
                            item.get(
                                "TYPE_NAME"
                            )
                        )
                        or "용량주의"
                    ),

                "caution":
                    prohibition,

                "remark":
                    remark,

                "notificationDate":
                    _safe_string(
                        item.get(
                            "NOTIFICATION_DATE"
                        )
                    ),

                "message":
                    (
                        f"{medicine_name}은 "
                        "식약처 DUR 용량주의 "
                        "대상 의약품입니다."
                    ),
            }
        )

    return {
        "success": True,

        "matchedRuleCount":
            len(
                matched_items
            ),

        "warnings":
            warnings,

        "error": None,
    }

def _get_efficacy_groups(
    medicine_name,
    ingredients,
    dataset_items,
):
    """
    DUR 효능군중복 데이터에서
    해당 약의 효능군(SERS_NAME)을 찾는다.
    """

    groups = []

    for item in dataset_items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        if not _dur_item_matches_drug(
            item,
            medicine_name,
            ingredients,
        ):
            continue

        sers_name = (
            _safe_string(
                item.get(
                    "SERS_NAME"
                )
            )
        )

        effect_name = (
            _safe_string(
                item.get(
                    "EFFECT_NAME"
                )
            )
        )

        if not sers_name:
            continue

        groups.append(
            {
                "sersName":
                    sers_name,

                "effectName":
                    effect_name,

                "ingredient":
                    (
                        _safe_string(
                            item.get(
                                "INGR_NAME"
                            )
                        )
                        or
                        _safe_string(
                            item.get(
                                "INGR_KOR_NAME"
                            )
                        )
                    ),

                "prohibition":
                    _safe_string(
                        item.get(
                            "PROHBT_CONTENT"
                        )
                    ),

                "remark":
                    _safe_string(
                        item.get(
                            "REMARK"
                        )
                    ),
            }
        )

    # 동일 효능군 중복 제거
    result = []

    seen = set()

    for group in groups:

        key = (
            group.get(
                "sersName"
            )
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            group
        )

    return result

def _check_efficacy_duplication(
    candidate_name,
    candidate_item_seq,
    candidate_ingredients,
    checked_medications,
):
    """
    새 약과 현재 복용약들의
    DUR 효능군(SERS_NAME)을 비교한다.
    """

    dataset = (
        _load_dur_dataset(
            DUR_EFFICACY_ENDPOINT
        )
    )

    if not dataset.get(
        "success"
    ):
        return {
            "success": False,
            "candidateGroups": [],
            "checkedMedicationCount": 0,
            "warnings": [],
            "error":
                dataset.get(
                    "error"
                ),
        }

    dataset_items = (
        dataset.get(
            "items",
            [],
        )
    )

    # ========================================
    # 새 약 효능군
    # ========================================

    candidate_groups = (
        _get_efficacy_groups(
            candidate_name,
            candidate_ingredients,
            dataset_items,
        )
    )

    candidate_group_map = {
        group["sersName"]:
            group
        for group in candidate_groups
        if group.get(
            "sersName"
        )
    }

    warnings = []

    checked_count = 0

    # ========================================
    # 기존 복용약과 비교
    # ========================================

    for medication in (
        checked_medications
    ):

        current_name = (
            _safe_string(
                medication.get(
                    "medicineName"
                )
            )
        )

        current_item_seq = (
            _safe_string(
                medication.get(
                    "itemSeq"
                )
            )
        )

        current_ingredients = (
            medication.get(
                "ingredients",
                [],
            )
        )

        if not isinstance(
            current_ingredients,
            list,
        ):
            current_ingredients = []

        # 같은 품목 자체는 효능군중복으로
        # 다시 경고하지 않는다.
        # 이미 동일성분 중복 검사에서 잡는다.
        if (
            candidate_item_seq
            and current_item_seq
            and candidate_item_seq
            == current_item_seq
        ):
            continue

        current_groups = (
            _get_efficacy_groups(
                current_name,
                current_ingredients,
                dataset_items,
            )
        )

        if not current_groups:
            continue

        checked_count += 1

        current_group_map = {
            group["sersName"]:
                group
            for group in current_groups
            if group.get(
                "sersName"
            )
        }

        common_groups = (
            set(
                candidate_group_map.keys()
            )
            &
            set(
                current_group_map.keys()
            )
        )

        for group_name in (
            common_groups
        ):

            candidate_group = (
                candidate_group_map[
                    group_name
                ]
            )

            current_group = (
                current_group_map[
                    group_name
                ]
            )

            warnings.append(
                {
                    "type":
                        "dur_efficacy_duplication",

                    "severity":
                        "warning",

                    "medicineName":
                        current_name,

                    "candidateMedicineName":
                        candidate_name,

                    "sersName":
                        group_name,

                    "effectName":
                        (
                            candidate_group.get(
                                "effectName"
                            )
                            or
                            current_group.get(
                                "effectName"
                            )
                            or ""
                        ),

                    "candidateIngredient":
                        candidate_group.get(
                            "ingredient",
                            "",
                        ),

                    "currentIngredient":
                        current_group.get(
                            "ingredient",
                            "",
                        ),

                    "prohibition":
                        (
                            candidate_group.get(
                                "prohibition"
                            )
                            or
                            current_group.get(
                                "prohibition"
                            )
                            or ""
                        ),

                    "remark":
                        (
                            candidate_group.get(
                                "remark"
                            )
                            or
                            current_group.get(
                                "remark"
                            )
                            or ""
                        ),

                    "message":
                        (
                            f"{candidate_name}과 "
                            f"{current_name}이 "
                            f"동일 효능군 "
                            f"'{group_name}'에 "
                            "해당합니다."
                        ),
                }
            )

    # ========================================
    # 경고 중복 제거
    # ========================================

    unique_warnings = []

    seen = set()

    for warning in warnings:

        key = (
            warning.get(
                "medicineName"
            ),
            warning.get(
                "sersName"
            ),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        unique_warnings.append(
            warning
        )

    return {
        "success": True,

        "candidateGroups":
            candidate_groups,

        "checkedMedicationCount":
            checked_count,

        "warnings":
            unique_warnings,

        "error": None,
    }

def _age_rule_applies(
    age,
    age_months,
    text,
):
    """
    DUR 금기 문구에서 숫자 연령을 읽어
    사용자가 해당되는지 검사한다.

    반환:
        True  -> 해당 금기
        False -> 해당하지 않음
        None  -> 자동 판정 불가
    """

    text = _safe_string(
        text
    )

    if not text:
        return None

    # =================================
    # 개월 기준
    # =================================

    match = re.search(
        r"(\d+)\s*개월\s*미만",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return (
            age_months
            < limit
        )

    match = re.search(
        r"(\d+)\s*개월\s*이하",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return (
            age_months
            <= limit
        )

    # =================================
    # 세 기준
    # =================================

    match = re.search(
        r"(\d+)\s*세\s*미만",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return age < limit

    match = re.search(
        r"(\d+)\s*세\s*이하",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return age <= limit

    match = re.search(
        r"(\d+)\s*세\s*초과",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return age > limit

    match = re.search(
        r"(\d+)\s*세\s*이상",
        text,
    )

    if match:
        limit = int(
            match.group(1)
        )
        return age >= limit

    # 소아/영아처럼
    # 정확한 숫자가 없는 경우
    return None

def _check_age_dur(
    user_id,
    medicine_name,
    ingredients,
):
    age_info = (
        _get_user_age(
            user_id
        )
    )

    if age_info is None:
        return {
            "success": False,
            "age": None,
            "warnings": [],
            "reviewRules": [],
            "error":
                "사용자 생년월일을 "
                "확인할 수 없습니다.",
        }

    dataset = (
        _load_dur_dataset(
            DUR_AGE_ENDPOINT
        )
    )

    if not dataset.get(
        "success"
    ):
        return {
            "success": False,
            "age":
                age_info["age"],
            "warnings": [],
            "reviewRules": [],
            "error":
                dataset.get(
                    "error"
                ),
        }

    matched_items = []

    for item in dataset.get(
        "items",
        [],
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue

        if _dur_item_matches_drug(
            item,
            medicine_name,
            ingredients,
        ):
            matched_items.append(
                item
            )

    warnings = []
    review_rules = []

    for item in matched_items:

        content = (
            _safe_string(
                item.get(
                    "PROHBT_CONTENT"
                )
            )
        )

        remark = (
            _safe_string(
                item.get(
                    "REMARK"
                )
            )
        )

        rule_text = (
            f"{content} {remark}"
        ).strip()

        applies = (
            _age_rule_applies(
                age_info["age"],
                age_info[
                    "ageMonths"
                ],
                rule_text,
            )
        )

        warning = {
            "type":
                "dur_age_contraindication",

            "severity":
                "danger",

            "medicineName":
                medicine_name,

            "ingredient":
                (
                    item.get(
                        "INGR_NAME"
                    )
                    or item.get(
                        "INGR_KOR_NAME"
                    )
                    or ""
                ),

            "userAge":
                age_info["age"],

            "prohibition":
                content,

            "remark":
                remark,

            "notificationDate":
                _safe_string(
                    item.get(
                        "NOTIFICATION_DATE"
                    )
                ),
        }

        if applies is True:

            warning["message"] = (
                f"현재 사용자는 만 "
                f"{age_info['age']}세이며, "
                f"{medicine_name}은 "
                "식약처 DUR 특정연령대 "
                "금기에 해당합니다."
            )

            warnings.append(
                warning
            )

        elif applies is None:

            warning[
                "severity"
            ] = "review"

            warning[
                "message"
            ] = (
                "특정연령대 금기 정보가 "
                "있지만 자동으로 정확한 "
                "연령 조건을 판정할 수 "
                "없는 문구입니다."
            )

            review_rules.append(
                warning
            )

    return {
        "success": True,

        "age":
            age_info["age"],

        "ageMonths":
            age_info[
                "ageMonths"
            ],

        "matchedRuleCount":
            len(
                matched_items
            ),

        "warnings":
            warnings,

        "reviewRules":
            review_rules,

        "error": None,
    }

def _call_dur_interaction_api(
    drug_a,
    drug_b,
):
    """
    식약처 DUR 병용금기 API 조회.

    drug_a:
        기준 약

    drug_b:
        함께 복용할 약
    """

    drug_a = _safe_string(
        drug_a
    )

    drug_b = _safe_string(
        drug_b
    )

    if not drug_a or not drug_b:
        return {
            "success": False,
            "items": [],
            "error":
                "약 이름이 비어 있습니다.",
        }

    if not DUR_API_KEY:
        return {
            "success": False,
            "items": [],
            "error":
                "DUR API 인증키가 "
                "설정되어 있지 않습니다.",
        }

    params = {
        "serviceKey":
            DUR_API_KEY,

        "type":
            "json",

        "pageNo":
            1,

        "numOfRows":
            100,

        "itemName":
            drug_a,

        "mixtureItemName":
            drug_b,
    }

    url = (
        DUR_API_URL
        + "?"
        + urlencode(params)
    )

    try:
        print(
            "[DUR] 병용금기 조회:",
            drug_a,
            "+",
            drug_b,
        )

        with urlopen(
            url,
            timeout=20,
        ) as response:

            body = (
                response
                .read()
                .decode(
                    "utf-8"
                )
            )

        data = json.loads(
            body
        )

        items = (
            _extract_dur_items(
                data
            )
        )

        print(
            "[DUR] 결과:",
            len(items),
            "건",
        )

        return {
            "success": True,
            "items": items,
            "error": None,
        }

    except Exception as e:

        print(
            "[DUR]"
            " 병용금기 API 오류:",
            drug_a,
            drug_b,
            str(e),
        )

        return {
            "success": False,
            "items": [],
            "error":
                str(e),
        }

def _normalize_dur_warning(
    item,
    current_medicine_name,
):
    """
    식약처 DUR 원본 데이터를
    앱에서 사용하기 쉬운 구조로 변경.
    """

    return {
        "type":
            "dur_contraindicated_combination",

        "severity":
            "danger",

        "ruleType":
            _safe_string(
                item.get(
                    "TYPE_NAME"
                )
            )
            or "병용금기",

        "currentMedicineName":
            current_medicine_name,

        # 기준 의약품
        "itemSeq":
            _safe_string(
                item.get(
                    "ITEM_SEQ"
                )
            ),

        "itemName":
            _safe_string(
                item.get(
                    "ITEM_NAME"
                )
            ),

        "ingredient":
            (
                _safe_string(
                    item.get(
                        "INGR_KOR_NAME"
                    )
                )
                or
                _safe_string(
                    item.get(
                        "MAIN_INGR"
                    )
                )
            ),

        # 병용금기 대상
        "mixtureItemSeq":
            _safe_string(
                item.get(
                    "MIXTURE_ITEM_SEQ"
                )
            ),

        "mixtureItemName":
            _safe_string(
                item.get(
                    "MIXTURE_ITEM_NAME"
                )
            ),

        "mixtureIngredient":
            (
                _safe_string(
                    item.get(
                        "MIXTURE_INGR_KOR_NAME"
                    )
                )
                or
                _safe_string(
                    item.get(
                        "MIXTURE_MAIN_INGR"
                    )
                )
            ),

        # DUR 공식 금기 설명
        "prohibition":
            _safe_string(
                item.get(
                    "PROHBT_CONTENT"
                )
            ),

        "remark":
            _safe_string(
                item.get(
                    "REMARK"
                )
            ),

        "notificationDate":
            _safe_string(
                item.get(
                    "NOTIFICATION_DATE"
                )
            ),

        "message":
            (
                f"{current_medicine_name}과 "
                "함께 복용 시 "
                "식약처 DUR 병용금기 정보가 "
                "확인되었습니다."
            ),
    }

def _check_dur_pair(
    candidate_name,
    current_name,
):
    """
    candidate + current 조합을 DUR로 검사.

    먼저:
        candidate → current

    없으면:
        current → candidate

    순서로 확인한다.
    """

    first = (
        _call_dur_interaction_api(
            candidate_name,
            current_name,
        )
    )

    # 첫 번째 호출에서 결과 발견
    if (
        first.get("success")
        and first.get("items")
    ):
        return {
            "success": True,
            "direction":
                "candidate_to_current",
            "items":
                first["items"],
            "error": None,
        }

    # 첫 API 호출 자체가 실패했다면
    # 반대방향을 호출해서 API 장애를
    # 없는 결과처럼 위장하지 않는다.
    first_error = (
        first.get("error")
    )

    second = (
        _call_dur_interaction_api(
            current_name,
            candidate_name,
        )
    )

    if (
        second.get("success")
        and second.get("items")
    ):
        return {
            "success": True,
            "direction":
                "current_to_candidate",
            "items":
                second["items"],
            "error": None,
        }

    # 둘 중 하나라도 정상 호출됐으면
    # 병용금기 없음으로 판단 가능
    if (
        first.get("success")
        or second.get("success")
    ):
        return {
            "success": True,
            "direction": None,
            "items": [],
            "error": None,
        }

    return {
        "success": False,
        "direction": None,
        "items": [],
        "error":
            (
                second.get("error")
                or first_error
                or "DUR 조회 실패"
            ),
    }
# ============================================================
# 성분 추출
# ============================================================

def _extract_ingredients(drug):
    """
    약 상세 API의 mainIngredient 또는
    raw.MATERIAL_NAME에서 성분명을 추출한다.
    """

    if not isinstance(drug, dict):
        return []

    result = []

    raw = drug.get("raw")

    if not isinstance(raw, dict):
        raw = {}

    # --------------------------------
    # 1. mainIngredient 우선
    # --------------------------------

    main_ingredient = _safe_string(
        drug.get("mainIngredient")
    )

    if main_ingredient:
        # 예:
        # [M203364]슈도에페드린염산염|
        # [M250460]트리프롤리딘염산염수화물

        main_ingredient = re.sub(
            r"\[[^\]]+\]",
            "",
            main_ingredient,
        )

        parts = re.split(
            r"[|;\n]",
            main_ingredient,
        )

        for part in parts:
            name = part.strip()

            if name:
                result.append(name)

    # --------------------------------
    # 2. MATERIAL_NAME fallback
    # --------------------------------

    if not result:
        material_name = _safe_string(
            raw.get("MATERIAL_NAME")
        )

        if material_name:
            matches = re.findall(
                r"성분명\s*:\s*([^|;]+)",
                material_name,
            )

            for match in matches:
                name = match.strip()

                if name:
                    result.append(name)

    # 중복 제거
    unique = []

    seen = set()

    for ingredient in result:

        key = _normalize_for_match(
            ingredient
        )

        if not key:
            continue

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            ingredient
        )

    return unique


# ============================================================
# 식약처 주의사항 ARTICLE 파싱
# ============================================================

def _extract_articles(document):
    document = _normalize_markup(
        document
    )

    if not document:
        return []

    article_regex = re.compile(
        r'<ARTICLE[^>]*title="([^"]*)"[^>]*>'
        r'(.*?)'
        r'</ARTICLE>',
        re.IGNORECASE
        | re.DOTALL,
    )

    paragraph_regex = re.compile(
        r"<PARAGRAPH[^>]*>"
        r"(.*?)"
        r"</PARAGRAPH>",
        re.IGNORECASE
        | re.DOTALL,
    )

    articles = []

    for match in article_regex.finditer(
        document
    ):
        title = _clean_document_text(
            match.group(1)
        )

        body = match.group(2)

        paragraphs = []

        for paragraph_match in (
            paragraph_regex.finditer(
                body
            )
        ):
            text = _clean_document_text(
                paragraph_match.group(1)
            )

            if text:
                paragraphs.append(
                    text
                )

        if not paragraphs:
            cleaned_body = (
                _clean_document_text(
                    body
                )
            )

            if cleaned_body:
                paragraphs.append(
                    cleaned_body
                )

        articles.append(
            {
                "title": title,
                "paragraphs": paragraphs,
            }
        )

    return articles


# ============================================================
# 금기사항
# ============================================================

def _extract_contraindications(
    precautions
):
    """
    식약처 문서에서
    '복용하지 말 것'
    '투여하지 말 것'
    등의 금기 항목 추출.
    """

    result = []

    articles = _extract_articles(
        precautions
    )

    keywords = [
        "복용하지 말",
        "투여하지 말",
        "사용하지 말",
        "금기",
    ]

    for article in articles:

        title = (
            article.get("title")
            or ""
        )

        if not any(
            keyword in title
            for keyword in keywords
        ):
            continue

        result.append(
            {
                "title": title,
                "items": article.get(
                    "paragraphs",
                    [],
                ),
            }
        )

    return result


# ============================================================
# 약물 상호작용 항목
# ============================================================

def _extract_interaction_articles(
    precautions
):
    """
    '이 약을 복용하는 동안 다음의 약을...'
    같은 약물 병용 관련 ARTICLE만 추출.
    """

    result = []

    articles = _extract_articles(
        precautions
    )

    keywords = [
        "다음의 약",
        "함께 복용",
        "병용",
        "동시 복용",
        "같이 복용",
    ]

    for article in articles:

        title = (
            article.get("title")
            or ""
        )

        body = " ".join(
            article.get(
                "paragraphs",
                [],
            )
        )

        full_text = (
            f"{title} {body}"
        )

        if not any(
            keyword in full_text
            for keyword in keywords
        ):
            continue

        result.append(
            {
                "title": title,
                "items": article.get(
                    "paragraphs",
                    [],
                ),
            }
        )

    return result


def _interaction_text(
    interaction_articles
):
    texts = []

    for article in interaction_articles:

        texts.append(
            article.get(
                "title",
                "",
            )
        )

        texts.extend(
            article.get(
                "items",
                [],
            )
        )

    return " ".join(texts)


# ============================================================
# 사용자의 현재 복용약 조회
# ============================================================

def _get_current_medications(
    user_id
):
    medicines = []

    # --------------------------------
    # 정기 복용약
    # --------------------------------

    schedule_docs = (
        db.collection("schedules")
        .where(
            "userId",
            "==",
            user_id,
        )
        .stream()
    )

    for doc in schedule_docs:

        data = (
            doc.to_dict()
            or {}
        )

        # active 명시적으로 false면 제외
        if data.get("active") is False:
            continue

        medicines.append(
            {
                "source": "schedule",
                "id": doc.id,
                "medicineName":
                    data.get(
                        "medicineName",
                        "",
                    ),
                "itemSeq":
                    data.get(
                        "itemSeq",
                    ),
                "ingredient":
                    data.get(
                        "ingredient",
                    ),
            }
        )

    # --------------------------------
    # 상비약
    # --------------------------------

    stock_docs = (
        db.collection(
            "stockMedicines"
        )
        .where(
            "userId",
            "==",
            user_id,
        )
        .stream()
    )

    for doc in stock_docs:

        data = (
            doc.to_dict()
            or {}
        )

        try:
            remaining = float(
                data.get(
                    "remainingCount",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):
            remaining = 0

        # 재고 없는 약 제외
        if remaining <= 0:
            continue

        medicines.append(
            {
                "source": "stock",
                "id": doc.id,
                "medicineName":
                    data.get(
                        "medicineName",
                        "",
                    ),
                "itemSeq":
                    data.get(
                        "itemSeq",
                    ),
                "ingredient":
                    data.get(
                        "ingredient",
                    ),
            }
        )

    # ========================================
    # 상호작용 검사용이므로
    # 같은 약은 한 번만 검사
    # ========================================

    unique_medicines = []

    seen = set()

    for medicine in medicines:

        medicine_name = (
            _safe_string(
                medicine.get(
                    "medicineName"
                )
            )
        )

        item_seq = (
            _safe_string(
                medicine.get(
                    "itemSeq"
                )
            )
        )

        key = (
            medicine_name
            .replace(" ", "")
            .lower(),
            item_seq,
        )

        if key in seen:
            continue

        seen.add(key)

        unique_medicines.append(
            medicine
        )

    return unique_medicines



# ============================================================
# 실제 검사
# ============================================================

def check_drug_interactions(
    user_id,
    candidate_item_seq,
):
    candidate = _get_drug_detail(
        candidate_item_seq
    )

    if candidate is None:
        return {
            "success": False,
            "message":
                "검사 대상 약 상세정보를 "
                "조회하지 못했습니다.",
        }

    candidate_name = _safe_string(
        candidate.get("itemName")
    )

    candidate_ingredients = (
        _extract_ingredients(
            candidate
        )
    )

    age_result = (
        _check_age_dur(
            user_id,
            candidate_name,
            candidate_ingredients,
        )
    )

    age_warnings = (
        age_result.get(
            "warnings",
            [],
        )
    )

    age_review_rules = (
        age_result.get(
            "reviewRules",
            [],
        )
    )

    pregnancy_result = (
        _check_pregnancy_dur(
            user_id,
            candidate_name,
            candidate_ingredients,
        )
    )

    pregnancy_warnings = (
        pregnancy_result.get(
            "warnings",
            [],
        )
    )

    dosage_result = (
        _check_dosage_dur(
            candidate_name,
            candidate_ingredients,
        )
    )

    dosage_warnings = (
        dosage_result.get(
            "warnings",
            [],
        )
    )

    candidate_precautions = (
        candidate.get(
            "precautions"
        )
        or (
            candidate.get("raw")
            or {}
        ).get(
            "NB_DOC_DATA"
        )
        or ""
    )

    contraindications = (
        _extract_contraindications(
            candidate_precautions
        )
    )

    candidate_interactions = (
        _extract_interaction_articles(
            candidate_precautions
        )
    )

    candidate_interaction_text = (
        _normalize_for_match(
            _interaction_text(
                candidate_interactions
            )
        )
    )

    current_medications = (
        _get_current_medications(
            user_id
        )
    )

    warnings = []

    # 식약처 DUR 공식 병용금기
    dur_warnings = []

    # DUR API 호출 실패 기록
    dur_errors = []

    # 실제 DUR 검사가 완료된 약 개수
    dur_checked_count = 0

    checked_medications = []

    skipped_medications = []

    # 후보약 성분 정규화
    candidate_ingredient_map = {
        _normalize_for_match(name): name
        for name
        in candidate_ingredients
        if _normalize_for_match(name)
    }

    for medication in (
        current_medications
    ):
        current_item_seq = (
            _safe_string(
                medication.get(
                    "itemSeq"
                )
            )
        )

        current_name = (
            _safe_string(
                medication.get(
                    "medicineName"
                )
            )
        )

        current_detail = None

# =================================
# 1. itemSeq가 있으면 바로 상세조회
# =================================

        if current_item_seq:
            current_detail = (
                _get_drug_detail(
                    current_item_seq
                )
            )

# =================================
# 2. 기존 약에 itemSeq가 없으면
#    약 이름으로 자동 검색
# =================================

        if (
            current_detail is None
            and current_name
        ):
            current_detail = (
                _search_drug_detail_by_name(
                    current_name
                )
            )

    # 검색으로 itemSeq를 찾았다면
    # 검사 결과에도 반영
            if current_detail:

                resolved_item_seq = (
                    _safe_string(
                        current_detail.get(
                            "itemSeq"
                        )
                    )
                )

                if resolved_item_seq:
                    current_item_seq = (
                        resolved_item_seq
                    )

        # --------------------------------
        # 기존 약 성분
        # --------------------------------

        current_ingredients = []

        if current_detail:
            current_ingredients = (
                _extract_ingredients(
                    current_detail
                )
            )

        # Firestore에 저장된 성분 fallback
        if not current_ingredients:

            saved_ingredient = (
                _safe_string(
                    medication.get(
                        "ingredient"
                    )
                )
            )

            if saved_ingredient:
                current_ingredients = [
                    saved_ingredient
                ]

        if not current_ingredients:
            skipped_medications.append(
                {
                    "medicineName":
                        current_name,
                    "itemSeq":
                        current_item_seq,
                    "reason":
                        "성분 정보를 확인할 수 없음",
                }
            )

            continue

        checked_medications.append(
            {
                "medicineName":
                    current_name,
                "itemSeq":
                    current_item_seq,
                "ingredients":
                    current_ingredients,
            }
        )

        # =================================
        # DUR 공식 병용금기 검사
        # =================================

        dur_result = (
            _check_dur_pair(
                candidate_name,
                current_name,
            )
        )

        if dur_result.get(
            "success"
        ):
            dur_checked_count += 1

            for dur_item in (
                dur_result.get(
                    "items",
                    [],
                )
            ):
                if not isinstance(
                    dur_item,
                    dict,
                ):
                    continue

                dur_warning = (
                    _normalize_dur_warning(
                        dur_item,
                        current_name,
                    )
                )

                dur_warning[
                    "direction"
                ] = dur_result.get(
                    "direction"
                )

                dur_warnings.append(
                    dur_warning
                )

        else:
            dur_errors.append(
                {
                    "medicineName":
                        current_name,

                    "error":
                        dur_result.get(
                            "error"
                        )
                        or "DUR 조회 실패",
                }
            )

        current_ingredient_map = {
            _normalize_for_match(name):
                name
            for name
            in current_ingredients
            if _normalize_for_match(name)
        }

        # =================================
        # 1. 동일 성분 중복 검사
        # =================================

        duplicate_keys = (
            set(
                candidate_ingredient_map
                .keys()
            )
            & set(
                current_ingredient_map
                .keys()
            )
        )

        for duplicate_key in (
            duplicate_keys
        ):
            ingredient_name = (
                candidate_ingredient_map[
                    duplicate_key
                ]
            )

            warnings.append(
                {
                    "type":
                        "duplicate_ingredient",

                    "severity":
                        "warning",

                    "medicineName":
                        current_name,

                    "ingredient":
                        ingredient_name,

                    "message":
                        (
                            f"{current_name}과 "
                            f"{candidate_name}에 "
                            f"동일 성분 "
                            f"'{ingredient_name}'이 "
                            f"포함되어 있습니다."
                        ),
                }
            )

        # =================================
        # 2. 새 약 주의사항에
        #    기존 약 성분이 등장하는지
        # =================================

        for normalized_name, original_name \
                in current_ingredient_map.items():

            # 너무 짧은 문자열 오탐 방지
            if len(
                normalized_name
            ) < 4:
                continue

            if (
                normalized_name
                in candidate_interaction_text
            ):
                warnings.append(
                    {
                        "type":
                            "interaction",

                        "severity":
                            "danger",

                        "medicineName":
                            current_name,

                        "ingredient":
                            original_name,

                        "direction":
                            "candidate_precautions",

                        "message":
                            (
                                f"{candidate_name}의 "
                                f"식약처 주의사항에서 "
                                f"{current_name}의 성분 "
                                f"'{original_name}'과 관련된 "
                                f"병용 주의가 확인되었습니다."
                            ),
                    }
                )

        # =================================
        # 3. 기존 약의 주의사항에
        #    새 약 성분이 등장하는지
        # =================================

        if current_detail:

            current_precautions = (
                current_detail.get(
                    "precautions"
                )
                or (
                    current_detail.get(
                        "raw"
                    )
                    or {}
                ).get(
                    "NB_DOC_DATA"
                )
                or ""
            )

            current_interactions = (
                _extract_interaction_articles(
                    current_precautions
                )
            )

            current_interaction_text = (
                _normalize_for_match(
                    _interaction_text(
                        current_interactions
                    )
                )
            )

            for normalized_name, original_name \
                    in candidate_ingredient_map.items():

                if len(
                    normalized_name
                ) < 4:
                    continue

                if (
                    normalized_name
                    in current_interaction_text
                ):
                    warnings.append(
                        {
                            "type":
                                "interaction",

                            "severity":
                                "danger",

                            "medicineName":
                                current_name,

                            "ingredient":
                                original_name,

                            "direction":
                                "current_medication_precautions",

                            "message":
                                (
                                    f"{current_name}의 "
                                    f"식약처 주의사항에서 "
                                    f"{candidate_name}의 성분 "
                                    f"'{original_name}'과 관련된 "
                                    f"병용 주의가 확인되었습니다."
                                ),
                        }
                    )

    # ========================================
    # DUR 효능군중복주의
    # ========================================

    efficacy_result = (
        _check_efficacy_duplication(
            candidate_name,
            candidate_item_seq,
            candidate_ingredients,
            checked_medications,
        )
    )

    efficacy_warnings = (
        efficacy_result.get(
            "warnings",
            [],
        )
    )

    # --------------------------------
    # 동일 경고 중복 제거
    # --------------------------------

    unique_warnings = []


    seen = set()

    for warning in warnings:

        key = (
            warning.get("type"),
            warning.get("medicineName"),
            warning.get("ingredient"),
            warning.get(
                "direction"),
        )

        if key in seen:
            continue

        seen.add(key)

        unique_warnings.append(
            warning
        )
    # ========================================
    # DUR 병용금기 중복 제거
    # ========================================

    unique_dur_warnings = []

    dur_seen = set()

    for warning in dur_warnings:

        key = (
            warning.get("itemSeq"),
            warning.get("mixtureItemSeq"),
            warning.get("ingredient"),
            warning.get("mixtureIngredient"),
            warning.get("prohibition"),
        )

        if key in dur_seen:
            continue

        dur_seen.add(key)

        unique_dur_warnings.append(
            warning
        )
    
    has_warning = (
        len(unique_warnings) > 0 or
        len(unique_dur_warnings) > 0 or
        len(age_warnings) > 0 or
        len(pregnancy_warnings) > 0 or
        len(dosage_warnings) > 0 or
        len(efficacy_warnings) > 0
    )


    return {
        "success": True,

        "candidate": {
            "itemSeq":
                _safe_string(
                    candidate.get(
                        "itemSeq"
                    )
                )
                or candidate_item_seq,

            "medicineName":
                candidate_name,

            "ingredients":
                candidate_ingredients,
        },

        # 검사된 기존 복용약
        "checkedMedicationCount":
            len(
                checked_medications
            ),

        "checkedMedications":
            checked_medications,

        # 정보 부족으로 검사 못한 약
        "skippedMedications":
            skipped_medications,

        # 새 약 자체의 공식 금기사항
        "contraindications":
            contraindications,

        # 공식 병용주의 원문
        "interactionSections":
            candidate_interactions,

        # 실제 기존 약과 비교한 결과
        "hasWarning":
            has_warning,

        "warnings":
            unique_warnings,
        # =====================================
        # 식약처 DUR 공식 병용금기
        # =====================================
        "durCheckedMedicationCount":
            dur_checked_count,

        "durWarnings":
            unique_dur_warnings,

        "durErrors":
            dur_errors,
        # =====================================
        # 특정연령대금기
        # =====================================

        "ageCheck": {
            "success":
                age_result.get(
                    "success",
                    False,
                ),

            "userAge":
                age_result.get(
                    "age"
                ),

            "matchedRuleCount":
                age_result.get(
                    "matchedRuleCount",
                    0,
                ),

            "error":
                age_result.get(
                    "error"
                ),
        },

        "ageWarnings":
            age_warnings,

        "ageReviewRules":
            age_review_rules,

        "pregnancyCheck": {
            "success":
                pregnancy_result.get(
                    "success",
                    False,
                ),

            "checked":
                pregnancy_result.get(
                    "checked",
                    False,
                ),

            "pregnant":
                pregnancy_result.get(
                    "pregnant"
                ),

            "gender":
                pregnancy_result.get(
                    "gender",
                    "",
                ),

            "matchedRuleCount":
                pregnancy_result.get(
                    "matchedRuleCount",
                    0,
                ),

            "error":
                pregnancy_result.get(
                    "error"
                ),
        },

        "pregnancyWarnings":
            pregnancy_warnings,

        # =====================================
        # 용량주의
        # =====================================

        "dosageCheck": {
            "success":
                dosage_result.get(
                    "success",
                    False,
                ),

            "matchedRuleCount":
                dosage_result.get(
                    "matchedRuleCount",
                    0,
                ),

            "error":
                dosage_result.get(
                    "error"
                ),
        },

        "dosageWarnings":
            dosage_warnings,

        # =====================================
        # 효능군중복주의
        # =====================================

        "efficacyDuplicationCheck": {
            "success":
                efficacy_result.get(
                    "success",
                    False,
                ),

            "candidateGroups":
                efficacy_result.get(
                    "candidateGroups",
                    [],
                ),

            "checkedMedicationCount":
                efficacy_result.get(
                    "checkedMedicationCount",
                    0,
                ),

            "warningCount":
                len(
                    efficacy_warnings
                ),

            "error":
                efficacy_result.get(
                    "error"
                ),
        },

        "efficacyWarnings":
            efficacy_warnings,

        # '안전'이라고 판정하지 않는다.
        "screeningStatus":
            (
                "pregnancy_contraindication"
                if pregnancy_warnings

                else "age_contraindication"
                if age_warnings

                else "dosage_caution"
                if dosage_warnings

                else "efficacy_duplication"
                if efficacy_warnings

                else "dur_interaction_warning"
                if unique_dur_warnings

                else "warning_found"
                if unique_warnings

                else "dur_check_incomplete"
                if dur_errors

                else "no_match_found"
            ),

        "message":
            (
                "사용자의 임신 상태와 관련된"
                "DUR 임부금기가 확인되었습니다."
                if pregnancy_warnings

                else
                "사용자의 연령과 관련된"
                "DUR 특정연령대금기가"
                "확인되었습니다."
                if age_warnings

                else
                "해당 의약품에 식약처 DUR"
                "용량주의 정보가 있습니다."
                if dosage_warnings

                else
                "식약처 DUR 병용금기가 "
                "확인되었습니다."
                if unique_dur_warnings

                else
                "동일 성분이 포함된 의약품이 "
                "현재 복용약에 있습니다."
                if any(
                    warning.get("type")
                    == "duplicate_ingredient"
                    for warning in unique_warnings
                )
                
                else
                "사용상주의사항 기반 "
                "병용 경고가 확인되었습니다."
                if unique_warnings

                else
                "동일 효능군의 의약품을 "
                "중복 복용할 가능성이 있습니다."
                if efficacy_warnings

                else
                "DUR 검사 중 일부 약의 "
                "조회에 실패했습니다."
                if dur_errors

                else
                "식약처 DUR에서 직접적인 "
                "병용금기가 확인되지 않았습니다."
            ),

        "notice":
            (
                "DUR 병용금기 결과는 식품의약품안전처 "
                "공공데이터를 이용한 조회 결과입니다. "
                "병용금기 결과가 없더라도 모든 임상적 "
                "상호작용이 없음을 의미하지 않습니다."
            ),
    }