import re

from firebase_config import db

from services.interaction_service import (
    _get_drug_detail,
)


# ============================================================
# 공통 문자열 처리
# ============================================================

def _safe_string(value):
    if value is None:
        return ""

    return str(value).strip()


def _normalize_text(value):
    """
    비교용 문자열 정규화.

    예:
    '슈도에페드린 염산염'
    '슈도에페드린염산염'
    을 비슷하게 비교할 수 있게 한다.
    """

    text = _safe_string(
        value
    ).lower()

    text = re.sub(
        r"[\s\-\_\(\)\[\]\{\},./]",
        "",
        text,
    )

    return text


# ============================================================
# 사용자 allergyList 조회
# ============================================================

def _get_user_allergies(
    user_id,
):
    try:
        user_doc = (
            db.collection("users")
            .document(user_id)
            .get()
        )

        if not user_doc.exists:
            return None, (
                "사용자 정보를 찾을 수 없습니다."
            )

        user = (
            user_doc.to_dict()
            or {}
        )

        raw_allergies = (
            user.get(
                "allergyList",
                [],
            )
        )

        # 문자열 하나로 저장되어 있어도 대응
        if isinstance(
            raw_allergies,
            str,
        ):
            raw_allergies = re.split(
                r"[,;/|]",
                raw_allergies,
            )

        if not isinstance(
            raw_allergies,
            list,
        ):
            raw_allergies = []

        allergies = []

        for allergy in raw_allergies:
            text = _safe_string(
                allergy
            )

            if not text:
                continue

            if text not in allergies:
                allergies.append(
                    text
                )

        return allergies, None

    except Exception as e:
        return None, str(e)


# ============================================================
# 약 상세조회 결과 정리
# ============================================================

def _normalize_drug_detail(
    detail_result,
    item_seq,
):
    if (
        not isinstance(
            detail_result,
            dict,
        )
        or not detail_result
    ):
        return None, (
            "약 상세정보 조회 실패"
        )

    if (
        detail_result.get(
            "success"
        )
        is False
    ):
        return None, (
            detail_result.get(
                "error"
            )
            or "약 상세정보 조회 실패"
        )

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


# ============================================================
# 성분 문자열 분리
# ============================================================

def _clean_ingredient_name(
    text,
):
    text = _safe_string(
        text
    )

    # [M203364] 같은 식약처 성분 코드 제거
    text = re.sub(
        r"\[[^\]]+\]",
        "",
        text,
    )

    return text.strip()


def _split_ingredient_field(
    value,
):
    """
    예:
    [M203364]슈도에페드린염산염|
    [M250460]트리프롤리딘염산염수화물
    """

    text = _safe_string(
        value
    )

    if not text:
        return []

    parts = re.split(
        r"[|;,]",
        text,
    )

    result = []

    for part in parts:
        ingredient = (
            _clean_ingredient_name(
                part
            )
        )

        if (
            ingredient
            and ingredient
            not in result
        ):
            result.append(
                ingredient
            )

    return result


def _extract_material_ingredients(
    material_name,
):
    """
    MATERIAL_NAME 예:

    총량 : 1정 중|
    성분명 : 슈도에페드린염산염|
    분량 : 60|...
    """

    text = _safe_string(
        material_name
    )

    if not text:
        return []

    matches = re.findall(
        r"성분명\s*:\s*([^|;]+)",
        text,
    )

    result = []

    for match in matches:
        ingredient = (
            _clean_ingredient_name(
                match
            )
        )

        if (
            ingredient
            and ingredient
            not in result
        ):
            result.append(
                ingredient
            )

    return result


def _extract_drug_ingredients(
    drug,
):
    """
    주성분 + 기타 식약처 성분 데이터를 모두 수집한다.
    """

    raw = (
        drug.get("raw")
        if isinstance(
            drug.get("raw"),
            dict,
        )
        else {}
    )

    ingredients = []

    sources = [
        drug.get(
            "mainIngredient"
        ),
        raw.get(
            "MAIN_ITEM_INGR"
        ),
        raw.get(
            "MAIN_INGR"
        ),
        raw.get(
            "INGR_NAME"
        ),
    ]

    for source in sources:
        for ingredient in (
            _split_ingredient_field(
                source
            )
        ):
            if (
                ingredient
                not in ingredients
            ):
                ingredients.append(
                    ingredient
                )

    # MATERIAL_NAME에서도 성분명 추출
    for ingredient in (
        _extract_material_ingredients(
            raw.get(
                "MATERIAL_NAME"
            )
        )
    ):
        if (
            ingredient
            not in ingredients
        ):
            ingredients.append(
                ingredient
            )

    return ingredients


# ============================================================
# allergy ↔ ingredient 비교
# ============================================================

def _match_allergies(
    allergies,
    ingredients,
):
    matches = []

    for allergy in allergies:
        normalized_allergy = (
            _normalize_text(
                allergy
            )
        )

        # 너무 짧은 문자열은
        # 오탐 가능성이 높으므로 제외
        if len(
            normalized_allergy
        ) < 2:
            continue

        for ingredient in ingredients:
            normalized_ingredient = (
                _normalize_text(
                    ingredient
                )
            )

            if not normalized_ingredient:
                continue

            match_type = None

            if (
                normalized_allergy
                == normalized_ingredient
            ):
                match_type = "exact"

            elif (
                len(
                    normalized_allergy
                ) >= 3
                and normalized_allergy
                in normalized_ingredient
            ):
                match_type = (
                    "allergy_in_ingredient"
                )

            elif (
                len(
                    normalized_ingredient
                ) >= 3
                and normalized_ingredient
                in normalized_allergy
            ):
                match_type = (
                    "ingredient_in_allergy"
                )

            if match_type:
                matches.append(
                    {
                        "allergy":
                            allergy,

                        "ingredient":
                            ingredient,

                        "matchType":
                            match_type,
                    }
                )

    # 중복 제거
    unique = []

    seen = set()

    for match in matches:
        key = (
            match["allergy"],
            match["ingredient"],
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            match
        )

    return unique


# ============================================================
# Swagger / API용 전체 검사
# ============================================================

def check_allergy(
    user_id,
    item_seq,
):
    # -------------------------------
    # 1. 사용자 알레르기 조회
    # -------------------------------

    allergies, user_error = (
        _get_user_allergies(
            user_id
        )
    )

    if user_error:
        return {
            "success": False,

            "userId":
                user_id,

            "itemSeq":
                item_seq,

            "error":
                user_error,
        }

    # -------------------------------
    # 2. 약 상세정보 조회
    # -------------------------------

    detail_result = (
        _get_drug_detail(
            item_seq
        )
    )

    drug, drug_error = (
        _normalize_drug_detail(
            detail_result,
            item_seq,
        )
    )

    if drug_error:
        return {
            "success": False,

            "userId":
                user_id,

            "itemSeq":
                item_seq,

            "error":
                drug_error,
        }

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

    # -------------------------------
    # 3. 약 성분 추출
    # -------------------------------

    ingredients = (
        _extract_drug_ingredients(
            drug
        )
    )

    # 사용자가 등록한 알레르기가 없음
    if not allergies:
        return {
            "success": True,

            "checked": True,

            "userId":
                user_id,

            "itemSeq":
                item_seq,

            "medicineName":
                medicine_name,

            "userAllergies": [],

            "ingredients":
                ingredients,

            "matchedAllergies": [],

            "hasWarning": False,

            "screeningStatus":
                "no_registered_allergy",

            "message":
                "등록된 알레르기 정보가 없습니다.",
        }

    # 성분을 가져오지 못함
    if not ingredients:
        return {
            "success": True,

            "checked": False,

            "userId":
                user_id,

            "itemSeq":
                item_seq,

            "medicineName":
                medicine_name,

            "userAllergies":
                allergies,

            "ingredients": [],

            "matchedAllergies": [],

            "hasWarning": False,

            "screeningStatus":
                "ingredient_not_found",

            "message":
                "의약품 성분 정보를 확인하지 못했습니다.",
        }

    # -------------------------------
    # 4. 비교
    # -------------------------------

    matches = (
        _match_allergies(
            allergies,
            ingredients,
        )
    )

    has_warning = bool(
        matches
    )

    return {
        "success": True,

        "checked": True,

        "userId":
            user_id,

        "itemSeq":
            item_seq,

        "medicineName":
            medicine_name,

        "userAllergies":
            allergies,

        "ingredients":
            ingredients,

        "matchedAllergies":
            matches,

        "hasWarning":
            has_warning,

        "screeningStatus":
            (
                "warning_found"
                if has_warning
                else "no_match"
            ),

        "message":
            (
                "사용자가 등록한 알레르기 항목과 "
                "의약품 성분이 일치합니다."
                if has_warning
                else
                "등록된 알레르기 항목과 직접 일치하는 "
                "의약품 성분이 확인되지 않았습니다."
            ),

        "notice":
            (
                "이 검사는 등록된 알레르기명과 "
                "식약처 성분명의 직접 문자열 비교입니다. "
                "같은 계열 약물의 교차 알레르기까지 "
                "판단하는 검사는 아닙니다."
            ),
    }


# ============================================================
# 기존 OCR 저장 코드 호환용
# ============================================================

def check_allergy_warning(
    user_id,
    medicine_name="",
    item_seq="",
):
    """
    기존 ocr.py가 사용하는 함수.

    itemSeq가 있으면 정확한 성분 검사 수행.
    없으면 기존 저장 흐름을 막지 않고 None 반환.
    """

    item_seq = _safe_string(
        item_seq
    )

    if not item_seq:
        return None

    result = check_allergy(
        user_id=user_id,
        item_seq=item_seq,
    )

    if not result.get(
        "success"
    ):
        return None

    if not result.get(
        "hasWarning"
    ):
        return None

    matched = (
        result.get(
            "matchedAllergies",
            [],
        )
    )

    matched_names = []

    for match in matched:
        ingredient = (
            match.get(
                "ingredient",
                "",
            )
        )

        if (
            ingredient
            and ingredient
            not in matched_names
        ):
            matched_names.append(
                ingredient
            )

    ingredient_text = (
        ", ".join(
            matched_names
        )
    )

    return (
        "알레르기 주의: "
        f"{ingredient_text}"
    )