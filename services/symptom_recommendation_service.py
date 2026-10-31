import html
import os
import re

import requests

from urllib.parse import unquote

from services.allergy_service import check_allergy
from services.market_recommendation import rank_with_market_data

# ============================================================
# 식약처 e약은요 API
# ============================================================

EASY_DRUG_API_URL = (
    "https://apis.data.go.kr/"
    "1471000/"
    "DrbEasyDrugInfoService/"
    "getDrbEasyDrugList"
)


MFDS_API_KEY = (
    os.getenv("MFDS_EASY_DRUG_API_KEY")
    or os.getenv("MFDS_API_KEY")
    or os.getenv("DATA_GO_KR_SERVICE_KEY")
    or os.getenv("SERVICE_KEY")
    or ""
).strip()


MFDS_API_KEY = unquote(
    MFDS_API_KEY
)


# ============================================================
# 증상별 검색 키워드
#
# 약 이름을 하드코딩하는 것이 아니라
# 식약처 효능효과 검색에 사용할 단어만 정의한다.
# ============================================================

SYMPTOM_KEYWORDS = {
    "두통": [
        "두통",
    ],

    "발열": [
        "발열",
    ],

    "감기": [
        "감기",
    ],

    "인후통": [
        "인후통",
        "목의 통증",
    ],

    "기침": [
        "기침",
    ],

    "가래": [
        "가래",
    ],

    "콧물": [
        "콧물",
    ],

    "코막힘": [
        "코막힘",
    ],

    "알레르기": [
        "알레르기",
        "비염",
    ],

    "소화불량": [
        "소화불량",
    ],

    "속쓰림": [
        "속쓰림",
        "위산과다",
    ],

    "설사": [
        "설사",
    ],

    "변비": [
        "변비",
    ],

    "근육통": [
        "근육통",
    ],

    "치통": [
        "치통",
    ],

    "생리통": [
        "생리통",
        "월경통",
    ],
}


SYMPTOM_ALIASES = {
    "머리아픔": "두통",
    "머리통증": "두통",

    "열": "발열",
    "열남": "발열",

    "목아픔": "인후통",
    "목통증": "인후통",

    "코막힘": "코막힘",
    "코가막힘": "코막힘",

    "콧물": "콧물",
    "코물": "콧물",

    "배탈": "설사",

    "체함": "소화불량",
    "체했음": "소화불량",

    "가슴쓰림": "속쓰림",

    "월경통": "생리통",
}


# ============================================================
# 공통 함수
# ============================================================

def _safe_string(value):
    if value is None:
        return ""

    return str(value).strip()


def _normalize_text(value):
    text = _safe_string(
        value
    ).lower()

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    return text


def _clean_html(value):
    """
    e약은요 응답에 HTML 태그가 포함되는 경우 제거.
    """

    text = _safe_string(
        value
    )

    if not text:
        return ""

    text = html.unescape(
        text
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# 입력 증상 정리
# ============================================================

def _resolve_symptom(
    symptom,
):
    raw = _safe_string(
        symptom
    )

    compact = (
        raw
        .replace(" ", "")
        .lower()
    )

    canonical = (
        SYMPTOM_ALIASES.get(
            compact
        )
    )

    if canonical:
        return (
            canonical,
            SYMPTOM_KEYWORDS[
                canonical
            ],
        )

    for symptom_name in (
        SYMPTOM_KEYWORDS.keys()
    ):
        if (
            compact
            == symptom_name.replace(
                " ",
                "",
            ).lower()
        ):
            return (
                symptom_name,
                SYMPTOM_KEYWORDS[
                    symptom_name
                ],
            )

    # 정의되지 않은 증상도
    # 식약처 효능효과 검색어로 그대로 사용
    return (
        raw,
        [raw],
    )


# ============================================================
# e약은요 API 호출
# ============================================================

def _search_easy_drug(
    keyword,
    rows=30,
):
    if not MFDS_API_KEY:
        return None, (
            "식약처 API 키가 설정되어 있지 않습니다."
        )

    params = {
        "ServiceKey":
            MFDS_API_KEY,

        "pageNo":
            1,

        "numOfRows":
            rows,

        "efcyQesitm":
            keyword,

        "type":
            "json",
    }

    try:
        response = requests.get(
            EASY_DRUG_API_URL,
            params=params,
            timeout=15,
        )

    except Exception as e:
        return None, (
            "e약은요 API 조회 실패: "
            f"{type(e).__name__}"
        ) 
    if response.status_code != 200:
        error_body = (
            response.text[:1000]
            if response.text
            else ""
        )

        return None, (
            "e약은요 API 인증/호출 실패 "
            f"(HTTP {response.status_code}) "
            f"{error_body}"
        )


    try:
        data = response.json()

    except ValueError:
        return None, (
            "e약은요 API 응답이 "
            "JSON 형식이 아닙니다."
        )

    # API 형식이
    # body가 최상위에 있는 경우와
    # response.body인 경우 모두 대응
    body = data.get(
        "body"
    )

    if not isinstance(
        body,
        dict,
    ):
        response_data = (
            data.get(
                "response"
            )
            or {}
        )

        body = (
            response_data.get(
                "body"
            )
        )

    if not isinstance(
        body,
        dict,
    ):
        return None, (
            "e약은요 API 응답 형식을 "
            "확인할 수 없습니다."
        )

    items = (
        body.get(
            "items"
        )
        or []
    )

    if isinstance(
        items,
        dict,
    ):
        # 일부 API 응답이
        # {"item": [...]} 형태일 경우 대응
        items = (
            items.get(
                "item"
            )
            or []
        )

    if isinstance(
        items,
        dict,
    ):
        items = [
            items
        ]

    if not isinstance(
        items,
        list,
    ):
        items = []

    return items, None


# ============================================================
# 후보 점수 계산
# ============================================================

def _score_candidate(
    item,
    canonical_symptom,
    keywords,
):
    efficacy = _clean_html(
        item.get(
            "efcyQesitm"
        )
    )

    normalized_efficacy = (
        _normalize_text(
            efficacy
        )
    )

    score = 0

    matched_keywords = []

    normalized_symptom = (
        _normalize_text(
            canonical_symptom
        )
    )

    if (
        normalized_symptom
        and normalized_symptom
        in normalized_efficacy
    ):
        score += 5

    for keyword in keywords:
        normalized_keyword = (
            _normalize_text(
                keyword
            )
        )

        if (
            normalized_keyword
            and normalized_keyword
            in normalized_efficacy
        ):
            score += 3

            if (
                keyword
                not in matched_keywords
            ):
                matched_keywords.append(
                    keyword
                )

    return (
        score,
        matched_keywords,
    )

def _summarize_precautions(
    warning,
    caution,
    interaction_info,
    side_effect,
):
    """
    식약처 원문은 그대로 보존하고,
    화면 표시용 핵심 주의사항만 최대 4개 생성한다.
    """

    warning = _clean_html(
        warning
    )

    caution = _clean_html(
        caution
    )

    interaction_info = _clean_html(
        interaction_info
    )

    side_effect = _clean_html(
        side_effect
    )

    combined = " ".join(
        [
            warning,
            caution,
            interaction_info,
            side_effect,
        ]
    )

    summaries = []


    def add_summary(text):
        if (
            text
            and text
            not in summaries
        ):
            summaries.append(
                text
            )


    # ==========================================
    # 1. 다른 약과 병용 금지 / 주의
    # ==========================================

    interaction_labels = []

    interaction_keyword_map = [
        (
            "다른 감기약",
            "감기약",
        ),
        (
            "해열진통제",
            "해열진통제",
        ),
        (
            "항히스타민",
            "항히스타민제",
        ),
        (
            "진해거담제",
            "진해거담제",
        ),
        (
            "진정제",
            "진정제",
        ),
        (
            "수면제",
            "수면제",
        ),
        (
            "MAO",
            "MAO 억제제",
        ),
        (
            "항우울",
            "항우울제",
        ),
    ]

    for (
        keyword,
        label,
    ) in interaction_keyword_map:

        if (
            keyword
            in interaction_info
            and label
            not in interaction_labels
        ):
            interaction_labels.append(
                label
            )

    if interaction_labels:

        display_labels = (
            interaction_labels[:4]
        )

        add_summary(
            "·".join(
                display_labels
            )
            + "와의 병용 금지 또는 "
            "주의가 있습니다."
        )

    elif (
        "함께 복용하지 마십시오"
        in interaction_info
        or
        "같이 복용하지 마십시오"
        in interaction_info
    ):
        add_summary(
            "함께 복용하면 안 되는 약이 "
            "있으므로 병용 주의사항을 "
            "확인하세요."
        )


    # ==========================================
    # 2. 임신 / 질환 관련 주의
    # ==========================================

    risk_labels = []

    risk_keyword_map = [
        (
            "임부",
            "임신",
        ),
        (
            "임신",
            "임신",
        ),
        (
            "수유부",
            "수유",
        ),
        (
            "고혈압",
            "고혈압",
        ),
        (
            "심장질환",
            "심장질환",
        ),
        (
            "관상심장질환",
            "심장질환",
        ),
        (
            "녹내장",
            "녹내장",
        ),
        (
            "당뇨병",
            "당뇨병",
        ),
        (
            "간장질환",
            "간질환",
        ),
        (
            "신장질환",
            "신장질환",
        ),
        (
            "신기능 장애",
            "신장질환",
        ),
        (
            "갑상선",
            "갑상선질환",
        ),
    ]

    for (
        keyword,
        label,
    ) in risk_keyword_map:

        if (
            keyword
            in caution
            and label
            not in risk_labels
        ):
            risk_labels.append(
                label
            )

    if risk_labels:

        display_labels = (
            risk_labels[:4]
        )

        add_summary(
            "·".join(
                display_labels
            )
            + " 관련 상태가 있다면 "
            "복용 전 의사·약사와 "
            "상담하세요."
        )


    # ==========================================
    # 3. 졸음 / 운전 주의
    # ==========================================

    if (
        "졸음"
        in combined
        and (
            "운전"
            in combined
            or
            "기계조작"
            in combined
            or
            "기계 조작"
            in combined
        )
    ):
        add_summary(
            "복용 후 졸음이 올 수 있어 "
            "운전·기계 조작에 주의하세요."
        )


    # ==========================================
    # 4. 음주 주의
    # ==========================================

    if (
        "음주하지 마십시오"
        in combined
        or
        "음주하지 마세요"
        in combined
        or
        "복용 중에는 음주"
        in combined
    ):
        add_summary(
            "복용 중에는 음주를 피하세요."
        )


    # ==========================================
    # 5. 아세트아미노펜 최대 용량
    # ==========================================

    acetaminophen_max = re.search(
        r"아세트아미노펜.*?"
        r"(?:일일|1일).*?"
        r"최대.*?"
        r"([\d,]+)\s*mg",
        combined,
        flags=re.IGNORECASE,
    )

    if acetaminophen_max:

        max_amount = (
            acetaminophen_max
            .group(1)
        )

        add_summary(
            "아세트아미노펜은 "
            f"1일 최대 {max_amount}mg을 "
            "넘지 마세요."
        )


    # ==========================================
    # 6. 심한 이상반응
    # ==========================================

    severe_reaction = (
        "즉각 중지"
        in combined
        or
        "즉시 복용을 중단"
        in combined
        or
        "즉각 중단"
        in combined
    )

    severe_keywords = (
        "호흡곤란"
        in combined
        or
        "스티븐스"
        in combined
        or
        "독성표피"
        in combined
        or
        "쇽"
        in combined
    )

    if (
        severe_reaction
        and severe_keywords
    ):
        add_summary(
            "발진·호흡곤란 등 심한 "
            "이상반응이 나타나면 "
            "복용을 중단하고 "
            "의사·약사와 상담하세요."
        )


    # ==========================================
    # 최대 4개만 표시
    # ==========================================

    return summaries[:4]

# ============================================================
# 결과 형식 변환
# ============================================================

def _make_candidate(
    item,
    score,
    matched_keywords,
):
    warning = _clean_html(
        item.get(
            "atpnWarnQesitm"
        )
    )

    caution = _clean_html(
        item.get(
            "atpnQesitm"
        )
    )

    interaction_info = _clean_html(
        item.get(
            "intrcQesitm"
        )
    )

    side_effect = _clean_html(
        item.get(
            "seQesitm"
        )
    )

    precaution_summary = (
        _summarize_precautions(
            warning=warning,
            caution=caution,
            interaction_info=
                interaction_info,
            side_effect=
                side_effect,
        )
    )

    return {
        "itemSeq":
            _safe_string(
                item.get(
                    "itemSeq"
                )
            ),

        "medicineName":
            _safe_string(
                item.get(
                    "itemName"
                )
            ),

        "manufacturer":
            _safe_string(
                item.get(
                    "entpName"
                )
            ),

        "efficacy":
            _clean_html(
                item.get(
                    "efcyQesitm"
                )
            ),

        "useMethod":
            _clean_html(
                item.get(
                    "useMethodQesitm"
                )
            ),

        # 화면에 먼저 보여줄
        # 간단한 핵심 주의사항
        "precautionSummary":
            precaution_summary,

        # 아래는 식약처 원문 그대로 유지
        "warning":
            warning,

        "caution":
            caution,

        "interactionInfo":
            interaction_info,

        "sideEffect":
            side_effect,

        "imageUrl":
            _safe_string(
                item.get(
                    "itemImage"
                )
            ),

        # e약은요는
        # 일반의약품 후보 검색용
        "purchaseType":
            "pharmacy",

        "matchScore":
            score,

        "matchedKeywords":
            matched_keywords,

        "source":
            "MFDS_EASY_DRUG",

        # 기존 상호작용 검사와
        # 나중에 연결하기 위한 표시
        "needsInteractionCheck":
            True,
    }


# ============================================================
# 증상별 추천
# ============================================================

def recommend_by_symptom(
    symptom,
    user_id=None,
    limit=5,
):
    symptom = _safe_string(
        symptom
    )

    user_id = _safe_string(
        user_id
    )

    if not symptom:
        return {
            "success": False,
            "error":
                "증상을 입력해주세요.",
        }

    try:
        limit = int(
            limit
        )

    except (
        ValueError,
        TypeError,
    ):
        limit = 5

    limit = max(
        1,
        min(
            limit,
            10,
        ),
    )

    (
        canonical_symptom,
        keywords,
    ) = _resolve_symptom(
        symptom
    )

    # --------------------------------
    # 1. 식약처 효능효과 검색
    # --------------------------------

    all_items = []

    seen_item_seq = set()

    api_errors = []

    for keyword in keywords:
        items, error = (
            _search_easy_drug(
                keyword,
                rows=30,
            )
        )

        if error:
            api_errors.append(
                {
                    "keyword":
                        keyword,

                    "error":
                        error,
                }
            )

            continue

        for item in items:
            if not isinstance(
                item,
                dict,
            ):
                continue

            item_seq = (
                _safe_string(
                    item.get(
                        "itemSeq"
                    )
                )
            )

            if not item_seq:
                continue

            if (
                item_seq
                in seen_item_seq
            ):
                continue

            seen_item_seq.add(
                item_seq
            )

            all_items.append(
                item
            )

    if (
        not all_items
        and api_errors
    ):
        return {
            "success": False,

            "symptom":
                symptom,

            "canonicalSymptom":
                canonical_symptom,

            "searchKeywords":
                keywords,

            "error":
                "식약처 의약품 검색에 실패했습니다.",

            "apiErrors":
                api_errors,
        }

    # --------------------------------
    # 2. 효능효과 기준 점수 계산
    # --------------------------------

    scored = []

    for item in all_items:
        (
            score,
            matched_keywords,
        ) = _score_candidate(
            item,
            canonical_symptom,
            keywords,
        )

        # 실제 효능효과에
        # 검색 증상이 없는 항목은 제외
        if score <= 0:
            continue

        candidate = (
            _make_candidate(
                item,
                score,
                matched_keywords,
            )
        )

        scored.append(
            candidate
        )

    scored.sort(
        key=lambda x: (
            x.get(
                "matchScore",
                0,
            )
        ),
        reverse=True,
    )

    # 알레르기 제외 때문에
    # 필요한 수보다 넉넉하게 검사
    candidate_pool = (
        scored[
            :max(
                limit * 3,
                10,
            )
        ]
    )

    recommendations = []

    excluded = []

    # --------------------------------
    # 3. 사용자 알레르기 검사
    # --------------------------------

    for candidate in (
        candidate_pool
    ):
        if (
            len(
                recommendations
            )
            >= limit
        ):
            break

        item_seq = (
            candidate[
                "itemSeq"
            ]
        )

        # userId가 없는 경우에는
        # 일반적인 후보만 반환
        if not user_id:
            candidate[
                "allergyCheck"
            ] = {
                "status":
                    "not_requested",

                "hasWarning":
                    False,
            }

            recommendations.append(
                candidate
            )

            continue

        allergy_result = (
            check_allergy(
                user_id=user_id,
                item_seq=item_seq,
            )
        )

        # 알레르기 검사가 실패하거나 완료되지 않은 경우 추천 제외
        if (
            not allergy_result.get("success")
            or allergy_result.get("checked") is not True
        ):
            candidate["allergyCheck"] = {
                "status": allergy_result.get(
                    "screeningStatus", "check_failed"
                ),
                "hasWarning": False,
                "checked": False,
                "matchedAllergies": [],
            }

            candidate["excludeReason"] = (
                "의약품 성분 또는 알레르기 검사 결과를 "
                "확인할 수 없어 추천에서 제외했습니다."
            )

            excluded.append(candidate)
            continue

        # 알레르기 검사가 실패했다면
        # 안전하게 추천 목록에서 제외
        if not allergy_result.get(
            "success"
        ):
            candidate[
                "allergyCheck"
            ] = {
                "status":
                    "check_failed",

                "hasWarning":
                    None,

                "error":
                    allergy_result.get(
                        "error"
                    ),
            }

            candidate[
                "excludeReason"
            ] = (
                "알레르기 검사를 "
                "완료하지 못했습니다."
            )

            excluded.append(
                candidate
            )

            continue

        has_warning = bool(
            allergy_result.get(
                "hasWarning"
            )
        )

        candidate[
            "allergyCheck"
        ] = {
            "status":
                allergy_result.get(
                    "screeningStatus"
                ),

            "hasWarning":
                has_warning,

            "matchedAllergies":
                allergy_result.get(
                    "matchedAllergies",
                    [],
                ),
        }

        # 알레르기 성분이 확인되면
        # 추천 목록에서는 제외
        if has_warning:
            candidate[
                "excludeReason"
            ] = (
                "사용자가 등록한 "
                "알레르기 성분과 "
                "일치합니다."
            )

            excluded.append(
                candidate
            )

            continue

        recommendations.append(
            candidate
        )
    # ========================================
    # 식약처 생산·수입실적 기반 추천 순서 보정
    # ========================================
    recommendations = rank_with_market_data(
        recommendations,
        relevance_field="matchScore",
        max_lookups=10,
    )
    return {
        "success": True,

        "symptom":
            symptom,

        "canonicalSymptom":
            canonical_symptom,

        "searchKeywords":
            keywords,

        "candidateCount":
            len(
                scored
            ),

        "recommendationCount":
            len(
                recommendations
            ),

        "recommendations":
            recommendations,

        "excludedCount":
            len(
                excluded
            ),

        "excluded":
            excluded,

        "interactionCheckStatus":
            "not_checked",

        "message":
            (
                "식약처 효능·효과 정보를 기준으로 "
                "일반의약품 후보를 검색했습니다."
            ),

        "notice":
            (
                "이 결과는 증상에 대한 의약품 후보 정보이며 "
                "진단이나 처방이 아닙니다. "
                "현재 복용 중인 약과의 상호작용은 "
                "별도 DUR 검사가 필요합니다."
            ),
    }