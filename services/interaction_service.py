import html
import json
import hashlib
import os
import re
import time
from datetime import date
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from urllib.parse import (
    quote,
    urlencode,
    unquote,
)
from urllib.request import urlopen

from dotenv import load_dotenv

from firebase_config import db
from services.medicine_detail_cache import (
    fetch_medicine_detail_cached,
)
from services.medicine_detail_cache import (
    fetch_medicine_detail_cached,
    save_medicine_detail_cache,
)
from google.cloud.firestore_v1.base_query import FieldFilter

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
    or os.getenv("MFDS_SERVICE_KEY")
    or os.getenv("MFDS_SERVICE_KEY")
    or os.getenv("DATA_GO_KR_SERVICE_KEY")
    or os.getenv("SERVICE_KEY")
    or ""
).strip()
_dur_dataset_cache = {}
DUR_DATASET_CACHE_HOURS = 24
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

KST = timezone(
    timedelta(hours=9)
)


def _is_current_schedule(
    schedule: dict,
) -> bool:
    """
    오늘 기준으로 아직 복용 기간이
    끝나지 않은 schedule만 True.
    """

    # 명시적으로 비활성화된 일정 제외
    if schedule.get("active") is False:
        return False

    end_date_raw = str(
        schedule.get(
            "endDate",
            "",
        )
        or ""
    ).strip()

    # 과거 데이터처럼 endDate가 없으면
    # 일단 검사 대상으로 유지
    if not end_date_raw:
        return True

    try:
        end_date = datetime.strptime(
            end_date_raw,
            "%Y-%m-%d",
        ).date()

        today = datetime.now(
            KST
        ).date()

        return end_date >= today

    except ValueError:
        # 이상한 과거 데이터 때문에
        # 안전검사를 건너뛰지는 않음
        return True
# ============================================================
# 약 상세정보 조회
# ============================================================
# 약 상세정보 캐시 (DUR 병용금기 캐시와 별개)
_drug_detail_cache = {}

# 약 상세정보를 메모리에 1시간 보관
DRUG_DETAIL_CACHE_SECONDS = 3600

def _get_drug_detail(item_seq):
    item_seq = _safe_string(item_seq)

    if not item_seq:
        return None

    now = datetime.now(timezone.utc)

    # ========================================
    # 1. 이미 조회한 상세정보가 있다면 재사용
    # ========================================
    cached = _drug_detail_cache.get(item_seq)

    if cached is not None:
        expires_at, drug = cached

        if now < expires_at:
            print(
                "[INTERACTION] 상세정보 캐시 사용:",
                item_seq,
            )
            return drug

        _drug_detail_cache.pop(item_seq, None)

    # ========================================
    # 2. 내부 상세 API 호출
    # ========================================
    url = (
        f"{BACKEND_INTERNAL_URL}"
        f"/drugs/{quote(item_seq)}"
    )

    # 식약처 API의 일시적인 응답 지연에 대응
    for attempt in range(1, 3):
        try:
            print(
                "[INTERACTION] 상세정보 조회:",
                item_seq,
                f"({attempt}/2)",
            )

            with urlopen(
                url,
                timeout=35,
            ) as response:
                body = response.read().decode(
                    "utf-8"
                )

            result = json.loads(body)

            if result.get("success") is not True:
                print(
                    "[INTERACTION] 상세 API 응답 실패:",
                    result.get("message"),
                )
                continue

            drug = result.get("drug")

            if not isinstance(drug, dict):
                print(
                    "[INTERACTION] 상세정보 형식 오류:",
                    item_seq,
                )
                continue

            # ====================================
            # 3. 성공한 데이터만 캐시 저장
            # ====================================
            _drug_detail_cache[item_seq] = (
                datetime.now(timezone.utc)
                + timedelta(
                    seconds=DRUG_DETAIL_CACHE_SECONDS
                ),
                drug,
            )

            print(
                "[INTERACTION] 상세정보 조회 성공:",
                item_seq,
            )

            return drug

        except Exception as e:
            print(
                "[INTERACTION] 상세정보 조회 오류:",
                item_seq,
                f"({attempt}/2)",
                type(e).__name__,
                str(e),
            )

    print(
        "[INTERACTION] 상세정보 조회 최종 실패:",
        item_seq,
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

        # 결과가 하나이고, 검색한 이름으로 시작할 때만 사용
        if (
            matched is None
            and len(medicines) == 1
            and isinstance(medicines[0], dict)
        ):
            only_drug = medicines[0]

            result_name = _safe_string(
                only_drug.get("itemName")
            )

            normalized_result = (
                result_name.replace(" ", "").lower()
            )

            if (
                target
                and normalized_result.startswith(target)
            ):
                matched = only_drug

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

        detail = fetch_medicine_detail_cached(
        item_seq=item_seq,
        fetcher=_get_drug_detail,
        )

        if not isinstance(detail, dict):
            return None

        # 검색으로 확인한 품목번호를 상세정보에도 보존
        detail = dict(detail)
        detail["itemSeq"] = item_seq

        return detail

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

def _load_dur_dataset(endpoint_name):
    """식약처 DUR 전체 데이터셋을 완전히 조회했을 때만 성공 및 캐시 저장."""

    now = datetime.now(timezone.utc)
    cached = _dur_dataset_cache.get(endpoint_name)

    if cached is not None:
        expires_at, items = cached
        if now < expires_at:
            return {
                "success": True,
                "items": items,
                "error": None,
            }
        _dur_dataset_cache.pop(endpoint_name, None)

    if not DUR_API_KEY:
        return {
            "success": False,
            "items": [],
            "error": "DUR API 인증키가 없습니다.",
        }

    page = 1
    page_size = 500
    max_pages = 100
    all_items = []
    expected_total = None
    seen_pages = set()

    try:
        while page <= max_pages:
            params = {
                "serviceKey": DUR_API_KEY,
                "type": "json",
                "pageNo": page,
                "numOfRows": page_size,
            }
            url = (
                f"{DUR_BASE_URL}/{endpoint_name}"
                f"?{urlencode(params)}"
            )

            with urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))

            if not isinstance(data, dict):
                raise ValueError("DUR API 응답이 JSON 객체가 아닙니다.")

            root = data.get("response", data)
            if not isinstance(root, dict):
                raise ValueError("DUR API response 형식 오류")

            header = root.get("header")
            body = root.get("body")
            if not isinstance(header, dict) or not isinstance(body, dict):
                raise ValueError("DUR API header/body 누락")

            result_code = str(header.get("resultCode", "")).strip()
            if result_code not in ("00", "0"):
                raise RuntimeError(
                    f"DUR API 오류: {result_code} / "
                    f"{header.get('resultMsg', '')}"
                )

            # 전체 건수가 누락되면 완전 조회 여부를 판단할 수 없다.
            try:
                total_count = int(body["totalCount"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("DUR API totalCount 누락 또는 형식 오류")
            if total_count < 0:
                raise ValueError("DUR API totalCount가 음수입니다.")

            if expected_total is None:
                expected_total = total_count
            elif expected_total != total_count:
                raise ValueError(
                    f"DUR API 전체 건수 변경: "
                    f"{expected_total} -> {total_count}"
                )

            returned_page = body.get("pageNo")
            if returned_page is not None and str(returned_page) != str(page):
                raise ValueError(
                    f"DUR API 페이지 불일치: 요청 {page}, 응답 {returned_page}"
                )

            items = _extract_dur_items(data)

            # totalCount=0은 결과가 실제로 0건일 때만 성공 가능.
            if expected_total == 0:
                if items:
                    raise ValueError("DUR API totalCount=0인데 items가 있습니다.")
                break

            if not items:
                raise ValueError(
                    f"DUR API {page}페이지 결과 누락: "
                    f"{len(all_items)}/{expected_total}건 수집"
                )

            # API가 이전 페이지를 되돌리는 경우 중복 누적 방지.
            signature = hashlib.sha256(
                json.dumps(
                    items,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            if signature in seen_pages:
                raise ValueError(f"DUR API {page}페이지 중복 반환")
            seen_pages.add(signature)

            all_items.extend(items)
            collected = len(all_items)

            if collected > expected_total:
                raise ValueError(
                    f"DUR API 수집 건수 초과: {collected}/{expected_total}"
                )
            if collected == expected_total:
                break

            # 한 페이지가 500건 미만이어도 전체 건수가 남으면 계속 조회.
            page += 1
        else:
            raise ValueError(
                f"DUR API 페이지 제한 {max_pages} 초과: "
                f"{len(all_items)}/{expected_total}건 수집"
            )

        if len(all_items) != expected_total:
            raise ValueError(
                f"DUR API 불완전 수집: "
                f"{len(all_items)}/{expected_total}건"
            )

        # 온전한 스냅샷만 캐시에 넣는다.
        _dur_dataset_cache[endpoint_name] = (
            datetime.now(timezone.utc)
            + timedelta(hours=DUR_DATASET_CACHE_HOURS),
            all_items,
        )
        print(
            f"[DUR DATASET] COMPLETE: {endpoint_name} "
            f"{len(all_items)}/{expected_total}건"
        )
        return {
            "success": True,
            "items": all_items,
            "error": None,
        }

    except Exception as e:
        print(
            "[DUR DATASET ERROR]",
            endpoint_name,
            type(e).__name__,
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

        today = datetime.now(KST).date()

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
    item_seq,
):

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

    seen_rules = set()

    target_seq = _safe_string(item_seq)
    target_name = _normalize_for_match(medicine_name)

    for item in dataset.get("items", []):
        if not isinstance(item, dict):
            continue

        source_seq = _safe_string(
            item.get("ITEM_SEQ")
        )

        source_name = _normalize_for_match(
            item.get("ITEM_NAME")
        )

        # 1. 품목번호가 모두 존재하면 정확히 비교
        if target_seq and source_seq:
            if target_seq != source_seq:
                continue

        # 2. 품목번호가 없는 경우 제품명으로 비교
        elif target_name and source_name:
            if target_name != source_name:
                continue

        else:
            continue

        # 3. 동일한 임부금기 규칙 중복 제거
        rule_key = (
            _safe_string(item.get("INGR_CODE")),
            _safe_string(item.get("PROHBT_CONTENT")),
            _safe_string(item.get("REMARK")),
            _safe_string(item.get("NOTIFICATION_DATE")),
            _safe_string(item.get("TYPE_NAME")),
        )

        if rule_key in seen_rules:
            continue

        seen_rules.add(rule_key)
        matched_items.append(item)

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
    item_seq,
):
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
    seen_rules = set()
    target_seq = _safe_string(item_seq)
    target_name = _normalize_for_match(medicine_name)

    for item in dataset.get("items", []):
        if not isinstance(item,dict):
            continue

        item_seq_value = _safe_string(
            item.get("ITEM_SEQ")
        )

        item_name_value = _normalize_for_match(
            item.get("ITEM_NAME")
        )

        # 품목번호 우선 비교
        if target_seq:
            if item_seq_value != target_seq:
                continue
        else:
            if item_name_value != target_name:
                continue

        rule_key = (
            _safe_string(item.get("INGR_CODE")),
            _safe_string(item.get("PROHBT_CONTENT")),
            _safe_string(item.get("REMARK")),
            _safe_string(item.get("NOTIFICATION_DATE")),
        )

        if rule_key in seen_rules:
            continue

        seen_rules.add(rule_key)
        matched_items.append(item)

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
    item_seq,
):
    age_info = _get_user_age(user_id)

    if age_info is None:
        return {
            "success": False,
            "age": None,
            "warnings": [],
            "reviewRules": [],
            "error": "사용자 생년월일을 확인할 수 없습니다.",
        }

    dataset = _load_dur_dataset(DUR_AGE_ENDPOINT)

    if not dataset.get("success"):
        return {
            "success": False,
            "age": age_info["age"],
            "warnings": [],
            "reviewRules": [],
            "error": dataset.get("error"),
        }

    target_seq = _safe_string(item_seq)
    target_name = _normalize_for_match(medicine_name)

    matched_items = []
    seen_rules = set()

    for item in dataset.get("items", []):
        if not isinstance(item, dict):
            continue

        source_seq = _safe_string(item.get("ITEM_SEQ"))
        source_name = _normalize_for_match(
            item.get("ITEM_NAME")
        )

        # 품목번호가 존재하면 품목번호로 비교
        if target_seq and source_seq:
            if target_seq != source_seq:
                continue
        elif target_name and source_name:
            if target_name != source_name:
                continue
        else:
            # 식별할 수 없는 규칙은 자동 판정에서 제외
            continue

        # 동일 규칙 중복 제거
        rule_key = (
            _safe_string(item.get("INGR_CODE")),
            _safe_string(item.get("PROHBT_CONTENT")),
            _safe_string(item.get("REMARK")),
            _safe_string(item.get("NOTIFICATION_DATE")),
        )

        if rule_key in seen_rules:
            continue

        seen_rules.add(rule_key)
        matched_items.append(item)

    warnings = []
    review_rules = []

    for item in matched_items:
        content = _safe_string(
            item.get("PROHBT_CONTENT")
        )
        remark = _safe_string(
            item.get("REMARK")
        )

        rule_text = f"{content} {remark}".strip()

        applies = _age_rule_applies(
            age_info["age"],
            age_info["ageMonths"],
            rule_text,
        )

        warning = {
            "type": "dur_age_contraindication",
            "severity": "danger",
            "medicineName": medicine_name,
            "ingredient": (
                item.get("INGR_NAME")
                or item.get("INGR_KOR_NAME")
                or ""
            ),
            "userAge": age_info["age"],
            "prohibition": content,
            "remark": remark,
            "notificationDate": _safe_string(
                item.get("NOTIFICATION_DATE")
            ),
        }

        if applies is True:
            warning["message"] = (
                f"현재 사용자는 만 {age_info['age']}세이며, "
                f"{medicine_name}은 식약처 DUR 특정연령대 "
                "금기에 해당합니다."
            )
            warnings.append(warning)

        elif applies is None:
            warning["severity"] = "review"
            warning["message"] = (
                "해당 의약품에 특정연령대 주의 정보가 "
                "있지만 연령 조건을 자동으로 "
                "확정할 수 없어 확인이 필요합니다."
            )
            review_rules.append(warning)

    return {
        "success": True,
        "age": age_info["age"],
        "ageMonths": age_info["ageMonths"],
        "matchedRuleCount": len(matched_items),
        "warnings": warnings,
        "reviewRules": review_rules,
        "error": None,
    }



def _call_dur_interaction_api(
    drug_a,
    drug_b,
    candidate_item_seq=None,
    current_item_seq=None,
):
    """전체 페이지와 양방향 병용금기 조회."""

    drug_a = _safe_string(drug_a).strip()
    drug_b = _safe_string(drug_b).strip()

    candidate_item_seq = _safe_string(
        candidate_item_seq
    ).strip()

    current_item_seq = _safe_string(
        current_item_seq
    ).strip()

    if not drug_a or not drug_b:
        return {
            "success": False,
            "items": [],
            "error": "약 이름이 없습니다.",
        }

    if not candidate_item_seq or not current_item_seq:
        return {
            "success": False,
            "items": [],
            "error": "품목번호가 없어 정확한 병용금기 검사가 불가능합니다.",
        }

    if not DUR_API_KEY:
        return {
            "success": False,
            "items": [],
            "error": "DUR API 인증키가 없습니다.",
        }

    page_size = 500
    max_pages_per_direction = 10

    # 병용금기 조회 전체에 허용할 최대 시간
    deadline = time.monotonic() + 40

    matched = []
    seen_matched = set()

    directions = [
        (drug_a, drug_b, candidate_item_seq),
        (drug_b, drug_a, current_item_seq),
    ]

    try:
        for base_name, mixture_name, base_item_seq in directions:

            page_no = 1
            read_rows = 0
            expected_total = None
            seen_pages = set()

            while True:
                remaining = deadline - time.monotonic()

                if remaining <= 0:
                    raise TimeoutError(
                        "병용금기 API 조회 제한 시간 초과"
                    )

                if page_no > max_pages_per_direction:
                    raise ValueError(
                        "DUR 조회 페이지 제한 초과: "
                        f"{base_name} + {mixture_name}"
                    )

                params = {
                    "serviceKey": DUR_API_KEY,
                    "type": "json",
                    "pageNo": page_no,
                    "numOfRows": page_size,
                    "itemSeq": base_item_seq,
                }

                url = DUR_API_URL + "?" + urlencode(params)

                with urlopen(
                    url,
                    timeout=min(12, max(0.1, remaining)),
                ) as response:
                    data = json.loads(
                        response.read().decode("utf-8")
                    )

                if not isinstance(data, dict):
                    raise ValueError(
                        "DUR API 응답이 JSON 객체가 아닙니다."
                    )

                root = data.get("response", data)

                if not isinstance(root, dict):
                    raise ValueError(
                        "DUR API response 형식 오류"
                    )

                header = root.get("header")
                body = root.get("body")

                if (
                    not isinstance(header, dict)
                    or not isinstance(body, dict)
                ):
                    raise ValueError(
                        "DUR API header/body 누락"
                    )

                result_code = str(
                    header.get("resultCode", "")
                ).strip()

                if result_code not in {"00", "0"}:
                    raise ValueError(
                        f"DUR API 오류: {result_code}, "
                        f"{header.get('resultMsg', '')}"
                    )

                try:
                    total_count = int(body["totalCount"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(
                        "DUR API totalCount 오류"
                    )

                if page_no == 1:
                    print(
                        "[DUR FILTER CHECK]",
                        f"drug_a={base_name}",
                        f"drug_b={mixture_name}",
                        f"totalCount={total_count}",
                        f"returnedRows={len(_extract_dur_items(data))}",
                    )

                if expected_total is None:
                    expected_total = total_count
                elif expected_total != total_count:
                    raise ValueError(
                        "조회 도중 totalCount 변경"
                    )

                returned_page = body.get("pageNo")

                if (
                    returned_page is not None
                    and str(returned_page) != str(page_no)
                ):
                    raise ValueError(
                        "DUR API 페이지 번호 불일치"
                    )

                page_items = _extract_dur_items(data)

                if total_count > read_rows and not page_items:
                    raise ValueError(
                        "DUR API 결과가 중간에 끊겼습니다."
                    )

                signature = hashlib.sha256(
                    json.dumps(
                        page_items,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()

                if signature in seen_pages and page_items:
                    raise ValueError(
                        "DUR API 동일 페이지 반복 반환"
                    )

                seen_pages.add(signature)
                read_rows += len(page_items)

                if read_rows > total_count:
                    raise ValueError(
                        "DUR API 결과 수 초과"
                    )

                pair_items = _filter_dur_pair_items(
                    page_items,
                    candidate_item_seq,
                    current_item_seq,
                )

                for item in pair_items:
                    fingerprint = json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )

                    if fingerprint not in seen_matched:
                        seen_matched.add(fingerprint)
                        matched.append(item)

                # 정확한 두 품목번호의 병용금기를 발견했다면
                # 이미 확인된 위험 경고를 즉시 반환
                if matched:
                    print(
                        "[DUR] 병용금기 발견, ", "추가 조회 생략:",
                        candidate_item_seq,
                        current_item_seq,
                    )

                    return {
                        "success": True,
                        "items": matched,
                        "error": None,
                        "queryComplete": False,
                        "matchFound": True,
                    }


                print(
                    f"[DUR] {base_name} + {mixture_name} "
                    f"{page_no}페이지 "
                    f"누적 {read_rows}/{total_count}"
                )

                if read_rows == total_count:
                    break

                page_no += 1

        return {
            "success": True,
            "items": matched,
            "error": None,
            "queryComplete": True,
            "matchFound": bool(matched),
        }

    except Exception as e:
        print(
            "[DUR] 병용금기 전체 조회 실패:",
            type(e).__name__,
            str(e),
        )

        return {
            "success": False,
            "items": [],
            "error": str(e),
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

# 검사 결과를 24시간 동안 재사용
DUR_CACHE_HOURS = 24


def _dur_pair_key(drug_a, drug_b):
    """
    A+B와 B+A를 같은 약물 쌍으로 취급.
    """
    names = sorted([
        " ".join(drug_a.strip().casefold().split()),
        " ".join(drug_b.strip().casefold().split()),
    ])

    raw_key = json.dumps(
        names,
        ensure_ascii=False,
    )

    cache_id = hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()

    return names, cache_id


def _get_dur_cache(user_id, cache_id):
    doc = (
        db.collection("users")
        .document(str(user_id))
        .collection("durPairCache")
        .document(cache_id)
        .get()
    )

    if not doc.exists:
        return None

    data = doc.to_dict() or {}

    expires_at = data.get("expiresAt")

    if expires_at is None:
        return None

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if datetime.now(timezone.utc) >= expires_at:
        return None

    result = data.get("result")

    if not isinstance(result, dict):
        return None

    if result.get("success") is not True:
        return None

    return result


def _save_dur_cache(user_id, cache_id, names, result):
    now = datetime.now(timezone.utc)

    (
        db.collection("users")
        .document(str(user_id))
        .collection("durPairCache")
        .document(cache_id)
        .set({
            "drugNames": names,
            "result": result,
            "checkedAt": now,
            "expiresAt": now + timedelta(
                hours=DUR_CACHE_HOURS
            ),
        })
    )


def _filter_dur_pair_items(
    items,
    candidate_item_seq,
    current_item_seq,
):
    """실제 검사 중인 두 품목의 병용금기만 반환."""

    expected = {
        str(candidate_item_seq or "").strip(),
        str(current_item_seq or "").strip(),
    }

    if "" in expected or len(expected) != 2:
        return []

    matched = []

    for item in items:
        if not isinstance(item, dict):
            continue

        item_a = str(
            item.get("ITEM_SEQ") or ""
        ).strip()

        item_b = str(
            item.get("MIXTURE_ITEM_SEQ") or ""
        ).strip()

        if {item_a, item_b} == expected:
            matched.append(item)

    return matched




def _check_dur_pair(
    user_id,
    candidate_name,
    current_name,
    candidate_item_seq,
    current_item_seq,
):
    """전체 조회 캐시와 조기 양성 캐시를 분리한다."""

    names, _ = _dur_pair_key(
        candidate_name,
        current_name,
    )

    seq_a = _safe_string(candidate_item_seq).strip()
    seq_b = _safe_string(current_item_seq).strip()

        # 같은 품목은 병용금기 쌍 검사에서 제외
    if seq_a and seq_b and seq_a == seq_b:
        return {
            "success": True,
            "direction": "same_product_skipped",
            "items": [],
            "queryComplete": True,
            "matchFound": False,
            "error": None,
        }

    # 필수 정보 누락
    if not all(names) or not seq_a or not seq_b:
        return {
            "success": False,
            "direction": None,
            "items": [],
            "queryComplete": False,
            "matchFound": False,
            "error": "약 이름 또는 품목번호가 누락되었습니다.",
        }

    # 조회 방식별로 서로 다른 Firestore 캐시 키 사용
    def cache_key(mode):
        raw = json.dumps(
            {
                "version": "dur_pair_cache_v5",
                "mode": mode,
                "names": names,
                "itemSeqs": sorted([seq_a, seq_b]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    # 1. 기존 캐시 확인
    for mode in ("complete", "early_positive"):
        try:
            cached = _get_dur_cache(
                user_id,
                cache_key(mode),
            )

        except Exception as exc:
            print(
                "[DUR CACHE] 조회 실패:",
                type(exc).__name__,
                str(exc),
            )
            continue

        if (
            not isinstance(cached, dict)
            or cached.get("success") is not True
        ):
            continue

        # 캐시 내용도 품목번호로 다시 필터링
        items = _filter_dur_pair_items(
            cached.get("items", []),
            seq_a,
            seq_b,
        )

        # 전체 조회를 완료한 캐시
        if (
            mode == "complete"
            and cached.get("queryComplete") is True
        ):
            print(
                "[DUR CACHE] COMPLETE HIT:",
                seq_a,
                seq_b,
            )

            return {
                **cached,
                "items": items,
                "matchFound": bool(items),
                "direction": "cached_complete",
            }

        # 조기 탐지한 병용금기 양성 캐시
        if (
            mode == "early_positive"
            and cached.get("queryComplete") is False
            and cached.get("matchFound") is True
            and items
        ):
            print(
                "[DUR CACHE] EARLY POSITIVE HIT:",
                seq_a,
                seq_b,
            )

            return {
                **cached,
                "items": items,
                "queryComplete": False,
                "matchFound": True,
                "direction": "cached_early_match",
            }

    # 2. 캐시가 없으면 식약처 API 호출
    api_result = _call_dur_interaction_api(
        candidate_name,
        current_name,
        candidate_item_seq=seq_a,
        current_item_seq=seq_b,
    )

    success = api_result.get("success") is True

    items = (
        _filter_dur_pair_items(
            api_result.get("items", []),
            seq_a,
            seq_b,
        )
        if success else []
    )

    complete = (
        success
        and api_result.get("queryComplete") is True
    )

    early_positive = (
        success
        and bool(items)
        and not complete
    )

    result = {
        "success": success,
        "direction": (
            "both_directions_full_pages"
            if complete
            else "early_match"
            if early_positive
            else "incomplete"
        ),
        "items": items,
        "error": api_result.get("error"),
        "queryComplete": complete,
        "matchFound": bool(items),
    }

    # 3. 완전 조회 또는 조기 양성만 캐시
    if complete or early_positive:
        mode = (
            "complete"
            if complete
            else "early_positive"
        )

        try:
            _save_dur_cache(
                user_id,
                cache_key(mode),
                names,
                result,
            )

            print(
                "[DUR CACHE] SAVED:",
                mode,
                seq_a,
                seq_b,
            )

        except Exception as exc:
            print(
                "[DUR CACHE] 저장 실패:",
                type(exc).__name__,
                str(exc),
            )

    return result

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
        filter=FieldFilter(
            "userId",
            "==",
            user_id,
        )
        )
        .stream()
    )

    for doc in schedule_docs:

        data = (
            doc.to_dict()
            or {}
        )
        if not _is_current_schedule(
            data
        ):
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
            filter=FieldFilter(
                "userId",
                "==",
                user_id,
            )
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
    candidate = fetch_medicine_detail_cached(
        item_seq=candidate_item_seq,
        fetcher=_get_drug_detail,
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
            candidate_item_seq,
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
            candidate_item_seq,
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
            candidate_item_seq,
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
    ##checked_dur_pairs = set()

    # 후보약 성분 정규화
    candidate_ingredient_map = {
        _normalize_for_match(name): name
        for name
        in candidate_ingredients
        if _normalize_for_match(name)
    }
    checked_dur_pairs = {}

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
            current_detail = fetch_medicine_detail_cached(
                item_seq=current_item_seq,
                fetcher=_get_drug_detail,
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
                    current_item_seq = resolved_item_seq

                    # 검색으로 확인한 품목번호를 기존 복용약에 저장
                    # 다음 DUR 검사부터 약 이름 검색을 생략
                    medicine_source = medication.get("source")
                    medicine_doc_id = medication.get("id")

                    collection_name = {
                        "schedule": "schedules",
                        "stock": "stockMedicines",
                    }.get(medicine_source)

                    if collection_name and medicine_doc_id:
                        try:
                            doc_ref = (
                                db.collection(collection_name)
                                .document(str(medicine_doc_id))
                            )

                            snapshot = doc_ref.get()

                            if snapshot.exists:
                                saved_data = snapshot.to_dict() or {}

                                # 다른 사용자의 문서가 아닌지 확인
                                same_user = (
                                    str(saved_data.get("userId", ""))
                                    == str(user_id)
                                )

                                # 기존 품목번호를 덮어쓰지 않음
                                saved_seq = str(
                                    saved_data.get("itemSeq") or ""
                                ).strip()

                                if same_user and not saved_seq:
                                    doc_ref.update({
                                        "itemSeq": resolved_item_seq
                                    })

                                    print(
                                        "[INTERACTION] 품목번호 자동 저장:",
                                        current_name,
                                        resolved_item_seq,
                                    )

                        except Exception as e:
                            # 저장 실패가 DUR 검사 자체를 중단시키지는 않음
                            print(
                                "[INTERACTION] 품목번호 저장 실패:",
                                current_name,
                                type(e).__name__,
                                str(e),
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

        same_drug = (
            bool(candidate_item_seq)
            and bool(current_item_seq)
            and str(candidate_item_seq) == str(current_item_seq)
        )

        if same_drug:
            print("[DUR] 동일 품목 조회 생략:", current_name)

            dur_result = {
                "success": True,
                "direction": "same_drug",
                "items": [],
                "error": None,
            }

        else:
            pair_names, _ = _dur_pair_key(
                candidate_name,
                current_name,
            )
            pair_id = (
                tuple(pair_names),
                tuple(sorted([
                    str(candidate_item_seq or "").strip(),
                    str(current_item_seq or "").strip(),
                ])),
            )

            if pair_id in checked_dur_pairs:
                dur_result = checked_dur_pairs[pair_id]

            else:
                dur_result = _check_dur_pair(
                    user_id,
                    candidate_name,
                    current_name,
                    candidate_item_seq,
                    current_item_seq,
                )

                checked_dur_pairs[pair_id] = dur_result
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
            set(candidate_ingredient_map.keys())
            & set(current_ingredient_map.keys())
        )

        same_drug = (
            bool(candidate_item_seq)
            and bool(current_item_seq)
            and str(candidate_item_seq).strip()
                == str(current_item_seq).strip()
        )

        if not same_drug:
            for duplicate_key in duplicate_keys:
                ingredient_name = candidate_ingredient_map[duplicate_key]

                warnings.append({
                    "type":"duplicate_ingredient",
                    "severity":"warning",
                    "medicineName": current_name,
                    "ingredient":ingredient_name,
                    "message":
                        (
                            f"{current_name}과 {candidate_name}에 "
                            f"동일 성분 '{ingredient_name}'이 포함되어 있습니다."
                        ),
                })

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
    
    check_errors = list(dur_errors)

    for check_name, check_result in (
        ("age", age_result),
        ("pregnancy", pregnancy_result),
        ("dosage", dosage_result),
        ("efficacy", efficacy_result),
    ):
        if check_result.get("success") is not True:
            check_errors.append({
                "check": check_name,
                "error": check_result.get("error") or "검사 실패",
            })

    if skipped_medications:
        check_errors.append({
            "check": "current_medications",
            "error": "성분 정보가 없어 검사하지 못한 복용약이 있습니다.",
        })

    
    # 임부금기 검사 대상이 아닌 경우와
    # 임신 정보가 부족한 경우를 구분
    pregnancy_gender = _safe_string(
        pregnancy_result.get("gender")
    ).strip().lower()

    pregnancy_not_applicable = (
        pregnancy_result.get("success") is True
        and pregnancy_result.get("checked") is False
        and pregnancy_result.get("pregnant") is None
        and pregnancy_gender in {
            "남",
            "남성",
            "male",
            "m",
        }
    )

    # 임부금기 검사 미완료 여부
    pregnancy_not_evaluated = (
        pregnancy_result.get("checked") is False
        and not pregnancy_not_applicable
    )

    # 검사 오류와 미완료 항목이 모두 없어야 완료
    screening_complete = (
        not check_errors
        and not pregnancy_not_evaluated
    )


    screening_complete = (
        not check_errors
        and not pregnancy_not_evaluated
    )

    if unique_dur_warnings:
        screening_status = "dur_interaction_warning"
        screening_message = "식약처 DUR 병용금기가 확인되었습니다."

    elif pregnancy_warnings:
        screening_status = "pregnancy_contraindication"
        screening_message = "임부금기가 확인되었습니다."

    elif age_warnings:
        screening_status = "age_contraindication"
        screening_message = "특정연령대금기가 확인되었습니다."

    elif check_errors:
        screening_status = "dur_check_incomplete"
        screening_message = "DUR 검사 중 일부 항목을 완료하지 못했습니다."

    elif age_review_rules:
        screening_status = "age_review_required"
        screening_message = "연령 조건에 대한 추가 확인이 필요합니다."

    elif any(
        w.get("type") == "duplicate_ingredient"
        for w in unique_warnings
    ):
        screening_status = "warning_found"
        screening_message = "동일 성분이 포함된 복용약이 있습니다."

    elif unique_warnings:
        screening_status = "warning_found"
        screening_message = "사용상주의사항 기반 병용 경고가 있습니다."

    elif efficacy_warnings:
        screening_status = "efficacy_duplication"
        screening_message = "동일 효능군 중복 가능성이 있습니다."

    elif dosage_warnings:
        screening_status = "dosage_caution"
        screening_message = "DUR 용량주의 정보가 있습니다."

    elif pregnancy_not_evaluated:
        screening_status = "dur_check_incomplete"
        screening_message = "임신 정보가 없어 임부금기를 판정하지 못했습니다."

    else:
        screening_status = "no_match_found"
        screening_message = "조회한 DUR 항목에서 해당 경고가 확인되지 않았습니다."


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

        "screeningStatus": screening_status,
        "message": screening_message,
        "screeningComplete": screening_complete,
        "checkErrors": check_errors,

        "notice":
            (
                "DUR 병용금기 결과는 식품의약품안전처 "
                "공공데이터를 이용한 조회 결과입니다. "
                "병용금기 결과가 없더라도 모든 임상적 "
                "상호작용이 없음을 의미하지 않습니다."
            ),
    }