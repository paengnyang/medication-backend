import re

from firebase_config import db

from services.drug_api_service import (
    get_drug_detail_by_item_seq,
    search_drug_by_name,
)
from datetime import date


# ============================================================
# 문자열 정규화
# ============================================================

def _normalize_text(value: str) -> str:
    """
    비교용 문자열 정리

    예:
    '슈도에페드린 염산염'
        -> '슈도에페드린염산염'
    """

    if value is None:
        return ""

    value = str(value).strip().lower()

    # 공백 제거
    value = re.sub(
        r"\s+",
        "",
        value,
    )

    # 비교에 불필요한 기호 제거
    value = re.sub(
        r"[^0-9a-z가-힣]",
        "",
        value,
    )

    return value


# ============================================================
# 사용자 알레르기 목록 정리
# ============================================================

def _normalize_allergy_list(
    allergy_value
):
    """
    Firestore allergyList가

    ["페니실린", "아스피린"]

    또는

    "페니실린, 아스피린"

    형태여도 처리한다.
    """

    if allergy_value is None:
        return []

    if isinstance(
        allergy_value,
        list,
    ):
        result = []

        for allergy in allergy_value:
            text = str(
                allergy
            ).strip()

            if text:
                result.append(
                    text
                )

        return result

    if isinstance(
        allergy_value,
        str,
    ):
        parts = re.split(
            r"[,/|]",
            allergy_value,
        )

        return [
            part.strip()
            for part in parts
            if part.strip()
        ]

    return []


# ============================================================
# 식약처 주성분 문자열 파싱
# ============================================================

def parse_main_ingredients(
    main_ingredient: str
):
    """
    식약처 데이터 예:

    [M203364]슈도에페드린염산염|
    [M250460]트리프롤리딘염산염수화물

    ↓

    [
        {
            "code": "M203364",
            "name": "슈도에페드린염산염"
        },
        {
            "code": "M250460",
            "name": "트리프롤리딘염산염수화물"
        }
    ]
    """

    if not main_ingredient:
        return []

    ingredients = []

    parts = str(
        main_ingredient
    ).split("|")

    for part in parts:

        part = part.strip()

        if not part:
            continue

        match = re.match(
            r"^\[([^\]]+)\](.+)$",
            part,
        )

        if match:

            code = (
                match
                .group(1)
                .strip()
            )

            name = (
                match
                .group(2)
                .strip()
            )

        else:

            code = None
            name = part

        if not name:
            continue

        ingredients.append({
            "code":
                code,

            "name":
                name,

            "normalizedName":
                _normalize_text(
                    name
                ),
        })

    return ingredients


# ============================================================
# 사용자 정보 조회
# ============================================================

def get_user_allergies(
    user_id: str
):
    user_ref = (
        db
        .collection("users")
        .document(user_id)
    )

    user_doc = user_ref.get()

    if not user_doc.exists:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

    user_data = (
        user_doc.to_dict()
        or {}
    )

    allergy_list = (
        _normalize_allergy_list(
            user_data.get(
                "allergyList",
                []
            )
        )
    )

    return allergy_list


# ============================================================
# 알레르기 문자열 비교
# ============================================================

def _is_allergy_match(
    allergy: str,
    ingredient: str,
):
    """
    현재 1차 버전은 문자열 기반 검사.

    ex)
    allergy:
        슈도에페드린염산염

    ingredient:
        슈도에페드린염산염
    """

    allergy_normalized = (
        _normalize_text(
            allergy
        )
    )

    ingredient_normalized = (
        _normalize_text(
            ingredient
        )
    )

    if (
        not allergy_normalized
        or not ingredient_normalized
    ):
        return False

    # 너무 짧은 단어의 부분일치로
    # 오탐이 생기는 것을 방지
    if len(
        allergy_normalized
    ) < 2:
        return False

    return (
        allergy_normalized
        in ingredient_normalized
        or ingredient_normalized
        in allergy_normalized
    )


# ============================================================
# 안전성 검사 메인 함수
# ============================================================

def check_medicine_safety(
    user_id: str,
    item_seqs: list[str],
):
    """
    사용자 알레르기 검사 +
    여러 약 사이 중복 성분 검사
    """

    user_id = str(
        user_id
    ).strip()

    if not user_id:
        raise ValueError(
            "userId가 필요합니다."
        )

    # --------------------------------
    # itemSeq 중복 제거
    # --------------------------------

    unique_item_seqs = []

    seen_seq = set()

    for value in item_seqs:

        item_seq = str(
            value
        ).strip()

        if not item_seq:
            continue

        if item_seq in seen_seq:
            continue

        seen_seq.add(
            item_seq
        )

        unique_item_seqs.append(
            item_seq
        )

    if not unique_item_seqs:
        raise ValueError(
            "검사할 의약품이 없습니다."
        )

    # --------------------------------
    # 사용자 알레르기 조회
    # --------------------------------

    user_allergies = (
        get_user_allergies(
            user_id
        )
    )

    medicines = []

    allergy_warnings = []

    lookup_errors = []

    # 성분 → 약 목록
    ingredient_map = {}

    # --------------------------------
    # 각 의약품 상세 조회
    # --------------------------------

    for item_seq in unique_item_seqs:

        try:

            drug = (
                get_drug_detail_by_item_seq(
                    item_seq
                )
            )

        except Exception as e:

            lookup_errors.append({
                "itemSeq":
                    item_seq,

                "error":
                    str(e),
            })

            continue

        if drug is None:

            lookup_errors.append({
                "itemSeq":
                    item_seq,

                "error":
                    "의약품 상세정보를 찾지 못했습니다.",
            })

            continue

        medicine_name = str(
            drug.get(
                "itemName",
                ""
            )
        ).strip()

        main_ingredient = (
            drug.get(
                "mainIngredient"
            )
        )

        ingredients = (
            parse_main_ingredients(
                main_ingredient
            )
        )

        medicine_info = {
            "itemSeq":
                item_seq,

            "medicineName":
                medicine_name,

            "ingredients": [
                {
                    "code":
                        ingredient[
                            "code"
                        ],

                    "name":
                        ingredient[
                            "name"
                        ],
                }
                for ingredient
                in ingredients
            ],
        }

        medicines.append(
            medicine_info
        )

        # =================================
        # 알레르기 검사
        # =================================

        for allergy in user_allergies:

            for ingredient in ingredients:

                ingredient_name = (
                    ingredient[
                        "name"
                    ]
                )

                if _is_allergy_match(
                    allergy,
                    ingredient_name,
                ):

                    allergy_warnings.append({
                        "itemSeq":
                            item_seq,

                        "medicineName":
                            medicine_name,

                        "allergy":
                            allergy,

                        "matchedIngredient":
                            ingredient_name,

                        "message":
                            (
                                f"{medicine_name}에 "
                                f"사용자의 알레르기 정보 "
                                f"'{allergy}'와 일치하는 "
                                f"성분이 포함되어 있습니다."
                            ),
                    })

        # =================================
        # 중복 성분 검사 준비
        # =================================

        for ingredient in ingredients:

            normalized_name = (
                ingredient[
                    "normalizedName"
                ]
            )

            if not normalized_name:
                continue

            if (
                normalized_name
                not in ingredient_map
            ):
                ingredient_map[
                    normalized_name
                ] = {
                    "ingredient":
                        ingredient[
                            "name"
                        ],

                    "medicines":
                        [],
                }

            # 같은 약 중복 추가 방지
            existing_names = [
                value[
                    "medicineName"
                ]
                for value
                in ingredient_map[
                    normalized_name
                ][
                    "medicines"
                ]
            ]

            if (
                medicine_name
                not in existing_names
            ):
                ingredient_map[
                    normalized_name
                ][
                    "medicines"
                ].append({
                    "itemSeq":
                        item_seq,

                    "medicineName":
                        medicine_name,
                })

    # ========================================================
    # 실제 중복된 성분만 경고
    # ========================================================

    duplicate_warnings = []

    for value in (
        ingredient_map.values()
    ):

        medicine_list = (
            value[
                "medicines"
            ]
        )

        if len(
            medicine_list
        ) < 2:
            continue

        duplicate_warnings.append({
            "ingredient":
                value[
                    "ingredient"
                ],

            "medicines":
                medicine_list,

            "message":
                (
                    f"{value['ingredient']} 성분이 "
                    f"{len(medicine_list)}개의 "
                    f"의약품에 중복되어 있습니다."
                ),
        })

    # ========================================================
    # 결과
    # ========================================================

    has_warning = bool(
        allergy_warnings
        or duplicate_warnings
    )

    return {
        "userId":
            user_id,

        "userAllergies":
            user_allergies,

        "checkedMedicineCount":
            len(medicines),

        "medicines":
            medicines,

        "allergyWarnings":
            allergy_warnings,

        "duplicateIngredientWarnings":
            duplicate_warnings,

        "lookupErrors":
            lookup_errors,

        "hasWarning":
            has_warning,
    }
# ============================================================
# 약 이름으로 ITEM_SEQ 찾기
# ============================================================

def _find_item_seq_by_medicine_name(
    medicine_name: str
):
    """
    기존 schedules에는 itemSeq가 없을 수 있으므로
    medicineName을 이용해 식약처 DB에서 ITEM_SEQ를 찾는다.
    """

    medicine_name = str(
        medicine_name
    ).strip()

    if not medicine_name:
        return None

    try:
        search_result = (
            search_drug_by_name(
                medicine_name
            )
        )

    except Exception as e:
        print(
            "[SAFETY ITEM_SEQ SEARCH ERROR]",
            medicine_name,
            str(e),
        )

        return None

    # --------------------------------
    # search_drug_by_name()이
    # List를 반환하는 경우
    # --------------------------------

    if isinstance(
        search_result,
        list,
    ):
        medicines = search_result

    # --------------------------------
    # 혹시 dict 형태인 경우도 대응
    # --------------------------------

    elif isinstance(
        search_result,
        dict,
    ):
        medicines = (
            search_result.get(
                "medicines"
            )
            or search_result.get(
                "items"
            )
            or []
        )

    else:
        medicines = []

    if not medicines:
        return None

    target_normalized = (
        _normalize_text(
            medicine_name
        )
    )

    # ========================================================
    # 1순위: 약 이름이 정확히 같은 결과
    # ========================================================

    for medicine in medicines:

        if not isinstance(
            medicine,
            dict,
        ):
            continue

        result_name = str(
            medicine.get(
                "itemName",
                ""
            )
        ).strip()

        if (
            _normalize_text(
                result_name
            )
            == target_normalized
        ):
            item_seq = (
                medicine.get(
                    "itemSeq"
                )
            )

            if item_seq:
                return str(
                    item_seq
                ).strip()

    # ========================================================
    # 2순위: 정확히 같은 게 없으면 첫 번째 결과
    # ========================================================

    first = medicines[0]

    if isinstance(
        first,
        dict,
    ):
        item_seq = (
            first.get(
                "itemSeq"
            )
        )

        if item_seq:
            return str(
                item_seq
            ).strip()

    return None


# ============================================================
# 현재 복용 중인지 확인
# ============================================================

def _is_current_schedule(
    schedule: dict
):
    """
    active, remainingCount, 시작일/종료일을 기준으로
    현재 복용 중인 일정인지 판단한다.
    """

    # active가 명시적으로 False면 제외
    if (
        schedule.get(
            "active",
            True
        )
        is False
    ):
        return False

    # 남은 약이 0 이하라면 제외
    remaining_count = (
        schedule.get(
            "remainingCount"
        )
    )

    if remaining_count is not None:

        try:
            if (
                float(
                    remaining_count
                )
                <= 0
            ):
                return False

        except (
            ValueError,
            TypeError,
        ):
            pass

    today = date.today()

    # --------------------------------
    # 시작일 확인
    # --------------------------------

    start_date = str(
        schedule.get(
            "startDate",
            ""
        )
    ).strip()

    if start_date:

        try:
            parsed_start = (
                date.fromisoformat(
                    start_date[:10]
                )
            )

            if parsed_start > today:
                return False

        except ValueError:
            pass

    # --------------------------------
    # 종료일 확인
    # --------------------------------

    end_date = str(
        schedule.get(
            "endDate",
            ""
        )
    ).strip()

    if end_date:

        try:
            parsed_end = (
                date.fromisoformat(
                    end_date[:10]
                )
            )

            if parsed_end < today:
                return False

        except ValueError:
            pass

    return True


# ============================================================
# 현재 복용 중인 의약품 조회
# ============================================================

def get_current_medications(
    user_id: str
):
    """
    Firestore schedules에서
    사용자가 현재 복용 중인 약을 가져온다.

    기존 schedule에 itemSeq가 없으면
    medicineName으로 식약처에서 찾아준다.
    """

    user_id = str(
        user_id
    ).strip()

    if not user_id:
        raise ValueError(
            "userId가 필요합니다."
        )

    schedules = (
        db
        .collection(
            "schedules"
        )
        .where(
            "userId",
            "==",
            user_id,
        )
        .stream()
    )

    medications = []

    unresolved = []

    seen_schedule_ids = set()

    for doc in schedules:

        schedule = (
            doc.to_dict()
            or {}
        )

        if not _is_current_schedule(
            schedule
        ):
            continue

        schedule_id = doc.id

        if (
            schedule_id
            in seen_schedule_ids
        ):
            continue

        seen_schedule_ids.add(
            schedule_id
        )

        medicine_name = str(
            schedule.get(
                "medicineName",
                ""
            )
        ).strip()

        if not medicine_name:
            continue

        # --------------------------------
        # 앞으로 itemSeq를 schedule에
        # 저장하게 되면 바로 사용
        # --------------------------------

        item_seq = str(
            schedule.get(
                "itemSeq",
                ""
            )
            or ""
        ).strip()

        # --------------------------------
        # 기존 데이터는 medicineName으로 찾음
        # --------------------------------

        if not item_seq:

            item_seq = (
                _find_item_seq_by_medicine_name(
                    medicine_name
                )
            )

        if not item_seq:

            unresolved.append({
                "scheduleId":
                    schedule_id,

                "medicineName":
                    medicine_name,

                "reason":
                    "식약처 품목기준코드를 찾지 못했습니다.",
            })

            continue

        medications.append({
            "scheduleId":
                schedule_id,

            "itemSeq":
                item_seq,

            "medicineName":
                medicine_name,

            "startDate":
                schedule.get(
                    "startDate"
                ),

            "endDate":
                schedule.get(
                    "endDate"
                ),

            "remainingCount":
                schedule.get(
                    "remainingCount"
                ),
        })

    return {
        "medications":
            medications,

        "unresolved":
            unresolved,
    }


# ============================================================
# 새 약 + 현재 복용약 안전성 검사
# ============================================================

def check_new_medicine_with_current(
    user_id: str,
    new_item_seq: str,
):
    """
    새로 등록하려는 약을
    현재 복용 중인 모든 약과 자동 비교한다.
    """

    user_id = str(
        user_id
    ).strip()

    new_item_seq = str(
        new_item_seq
    ).strip()

    if not user_id:
        raise ValueError(
            "userId가 필요합니다."
        )

    if not new_item_seq:
        raise ValueError(
            "newItemSeq가 필요합니다."
        )

    # ========================================================
    # 1. 새 약 상세정보
    # ========================================================

    new_drug = (
        get_drug_detail_by_item_seq(
            new_item_seq
        )
    )

    if new_drug is None:
        raise ValueError(
            "새로 등록하려는 의약품 정보를 찾지 못했습니다."
        )

    new_medicine_name = str(
        new_drug.get(
            "itemName",
            ""
        )
    ).strip()

    # ========================================================
    # 2. 현재 복용약 조회
    # ========================================================

    current_result = (
        get_current_medications(
            user_id
        )
    )

    current_medications = (
        current_result[
            "medications"
        ]
    )

    unresolved = (
        current_result[
            "unresolved"
        ]
    )

    # ========================================================
    # 3. 동일 의약품 자체가 이미 복용 중인지 검사
    # ========================================================

    duplicate_medicine_warnings = []

    for medicine in current_medications:

        if (
            medicine[
                "itemSeq"
            ]
            == new_item_seq
        ):

            duplicate_medicine_warnings.append({
                "itemSeq":
                    new_item_seq,

                "medicineName":
                    new_medicine_name,

                "scheduleId":
                    medicine[
                        "scheduleId"
                    ],

                "message":
                    (
                        f"{new_medicine_name}은(는) "
                        "현재 이미 복용 중인 의약품입니다."
                    ),
            })

    # ========================================================
    # 4. 현재약 ITEM_SEQ + 새 약 ITEM_SEQ 합치기
    # ========================================================

    all_item_seqs = [
        new_item_seq
    ]

    for medicine in current_medications:

        current_item_seq = (
            medicine[
                "itemSeq"
            ]
        )

        # 기존 check_medicine_safety는
        # ITEM_SEQ 중복을 제거하므로
        # 동일약 중복은 위에서 따로 검사한다.
        if (
            current_item_seq
            not in all_item_seqs
        ):
            all_item_seqs.append(
                current_item_seq
            )

    # ========================================================
    # 5. 기존 안전성 검사 재사용
    #
    # - 사용자 알레르기
    # - 중복 성분
    # ========================================================

    safety_result = (
        check_medicine_safety(
            user_id=
                user_id,

            item_seqs=
                all_item_seqs,
        )
    )

    # ========================================================
    # 6. 중복 성분 중
    # 실제로 "새 약"과 관련된 경고만 추출
    # ========================================================

    new_drug_duplicate_warnings = []

    for warning in (
        safety_result.get(
            "duplicateIngredientWarnings",
            []
        )
    ):

        medicines = (
            warning.get(
                "medicines",
                []
            )
        )

        contains_new_drug = any(
            medicine.get(
                "itemSeq"
            )
            == new_item_seq

            for medicine
            in medicines
        )

        if contains_new_drug:
            new_drug_duplicate_warnings.append(
                warning
            )

    # ========================================================
    # 7. 새 약의 알레르기 경고만 따로 추출
    # ========================================================

    new_drug_allergy_warnings = [
        warning

        for warning
        in safety_result.get(
            "allergyWarnings",
            []
        )

        if warning.get(
            "itemSeq"
        )
        == new_item_seq
    ]

    # ========================================================
    # 8. 최종 경고 여부
    # ========================================================

    has_warning = bool(
        duplicate_medicine_warnings
        or new_drug_duplicate_warnings
        or new_drug_allergy_warnings
    )

    # ========================================================
    # 9. 반환
    # ========================================================

    return {
        "userId":
            user_id,

        "newMedicine": {
            "itemSeq":
                new_item_seq,

            "medicineName":
                new_medicine_name,

            "ingredients":
                parse_main_ingredients(
                    new_drug.get(
                        "mainIngredient"
                    )
                ),
        },

        "currentMedicationCount":
            len(
                current_medications
            ),

        "currentMedications":
            current_medications,

        "unresolvedCurrentMedications":
            unresolved,

        "allergyWarnings":
            new_drug_allergy_warnings,

        "duplicateMedicineWarnings":
            duplicate_medicine_warnings,

        "duplicateIngredientWarnings":
            new_drug_duplicate_warnings,

        "hasWarning":
            has_warning,
    }