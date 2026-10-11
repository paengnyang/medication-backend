import html
import re

from services.interaction_service import (
    _get_drug_detail,
)


def _safe_string(value):
    if value is None:
        return ""

    return str(value).strip()


def _clean_usage_xml(text):
    text = _safe_string(text)

    if not text:
        return ""

    text = html.unescape(text)

    text = (
        text
        .replace(r"\<", "<")
        .replace(r"\>", ">")
        .replace(r"\"", '"')
    )

    return text


def _extract_articles(usage):
    """
    UD_DOC_DATA의 ARTICLE별로
    title + 문장 목록을 추출한다.
    """

    usage = _clean_usage_xml(
        usage
    )

    articles = []

    pattern = re.compile(
        r'<ARTICLE[^>]*'
        r'title="([^"]*)"'
        r'[^>]*>'
        r'(.*?)'
        r'</ARTICLE>',
        re.DOTALL,
    )

    for match in pattern.finditer(
        usage
    ):
        title = (
            match.group(1)
            .strip()
        )

        body = match.group(2)

        paragraphs = re.findall(
            r'<!\[CDATA\[(.*?)\]\]>',
            body,
            flags=re.DOTALL,
        )

        cleaned = []

        for paragraph in paragraphs:
            paragraph = (
                html.unescape(
                    paragraph
                )
                .replace(
                    "&#12316;",
                    "~",
                )
                .strip()
            )

            paragraph = re.sub(
                r"\s+",
                " ",
                paragraph,
            )

            if paragraph:
                cleaned.append(
                    paragraph
                )

        articles.append(
            {
                "title": title,
                "paragraphs": cleaned,
            }
        )

    return articles


def _select_article(
    drug,
    articles,
):
    """
    정제/캡슐/시럽 등
    현재 제품 제형에 맞는 ARTICLE 선택.
    """

    if not articles:
        return None

    item_name = _safe_string(
        drug.get(
            "itemName"
        )
    )

    raw = (
        drug.get("raw")
        if isinstance(
            drug.get("raw"),
            dict,
        )
        else {}
    )

    chart = _safe_string(
        raw.get(
            "CHART"
        )
    )

    target = (
        f"{item_name} {chart}"
    )

    keywords = []

    if (
        "시럽" in target
        or "액제" in target
    ):
        keywords = [
            "시럽",
            "액제",
        ]

    elif (
        "캡슐" in target
        or "캅셀" in target
    ):
        keywords = [
            "캡슐",
            "캅셀",
        ]

    elif (
        "정제" in target
        or item_name.endswith(
            "정"
        )
        or "정(" in item_name
    ):
        keywords = [
            "정제",
        ]

    for keyword in keywords:
        for article in articles:

            if keyword in (
                article.get(
                    "title",
                    ""
                )
            ):
                return article

    return articles[0]


def _extract_single_dose(
    paragraphs,
):
    """
    성인 1회 복용량 문장을 찾는다.
    """

    for text in paragraphs:

        if (
            "성인" in text
            and re.search(
                r"1\s*회",
                text,
            )
        ):
            return text

    for text in paragraphs:

        if re.search(
            r"1\s*회",
            text,
        ):
            return text

    return ""


def _extract_daily_frequency(
    paragraphs,
):
    """
    1일 몇 회 복용인지 찾는다.
    """

    for text in paragraphs:

        if re.search(
            r"(?:1\s*일|하루)"
            r"\s*\d+\s*회",
            text,
        ):
            return text

    return ""


def _extract_explicit_max_daily(
    paragraphs,
):
    """
    '1일 최대', '하루 최대',
    '초과하지 않는다'처럼
    최대량이 명시된 문장을 찾는다.
    """

    for text in paragraphs:

        if re.search(
            r"(?:1\s*일|하루)"
            r".{0,30}"
            r"(?:최대|최고)",
            text,
        ):
            return text

        if (
            re.search(
                r"(?:1\s*일|하루)",
                text,
            )
            and re.search(
                r"초과하지|넘지",
                text,
            )
        ):
            return text

    return ""


def _parse_dose_amount(
    text,
):
    """
    예:
    1회 1정
    1회 1~2정
    1회 1/2정
    """

    match = re.search(
        r"1\s*회\s*"
        r"(\d+(?:\.\d+)?"
        r"(?:/\d+)?"
        r"(?:\s*[~～\-]\s*"
        r"\d+(?:\.\d+)?)?)"
        r"\s*"
        r"(정|캡슐|캅셀|포|"
        r"mL|ml|밀리리터|"
        r"mg|밀리그램|g|그램)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    amount_text = (
        match.group(1)
        .replace(" ", "")
    )

    unit = match.group(2)

    # 범위면 큰 값을 최대값으로 사용
    parts = re.split(
        r"[~～\-]",
        amount_text,
    )

    value_text = parts[-1]

    try:
        if "/" in value_text:
            numerator, denominator = (
                value_text.split(
                    "/",
                    1,
                )
            )

            value = (
                float(numerator)
                / float(denominator)
            )

        else:
            value = float(
                value_text
            )

    except (
        ValueError,
        ZeroDivisionError,
    ):
        return None

    return {
        "value": value,
        "unit": unit,
    }


def _parse_daily_count(
    text,
):
    match = re.search(
        r"(?:1\s*일|하루)"
        r"\s*(\d+)\s*회",
        text,
    )

    if not match:
        return None

    try:
        return int(
            match.group(1)
        )

    except ValueError:
        return None


def get_dose_info(
    item_seq,
):
    detail_result = (
        _get_drug_detail(
            item_seq
        )
    )

    if (
        not isinstance(
            detail_result,
            dict,
        )
        or not detail_result
    ):
        return {
            "success": False,
            "itemSeq": item_seq,
            "error":
                 "약 상세정보 조회 실패",
        }

    if detail_result.get("success") is False:
        return {
        "success": False,
        "itemSeq": item_seq,
        "error":
            detail_result.get("error")
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


    usage = (
        _safe_string(
            drug.get(
                "usage"
            )
            or raw.get(
                "UD_DOC_DATA"
            )
        )
    )

    articles = (
        _extract_articles(
            usage
        )
    )

    article = (
        _select_article(
            drug,
            articles,
        )
    )

    if not article:
        return {
            "success": True,
            "itemSeq": item_seq,
            "medicineName":
                medicine_name,

            "singleDoseText": "",

            "dailyFrequencyText": "",

            "calculatedDailyDoseText": "",

            "maxDailyDoseText": "",

            "maxDailyDoseSource":
                "not_found",

            "usage": usage,
        }

    paragraphs = (
        article.get(
            "paragraphs",
            [],
        )
    )

    single_dose = (
        _extract_single_dose(
            paragraphs
        )
    )

    daily_frequency = (
        _extract_daily_frequency(
            paragraphs
        )
    )

    explicit_max = (
        _extract_explicit_max_daily(
            paragraphs
        )
    )

    max_daily_text = ""
    max_daily_source = "not_explicit"

    calculated_daily_dose_text = ""

    # ===================================
    # 공식 문서에 최대량이 직접 있는 경우
    # ===================================

    if explicit_max:

        max_daily_text = explicit_max

        max_daily_source = "explicit"

    # ===================================
    # 직접 최대량 표기가 없으면
    # 1회량 × 1일 횟수 계산
    # ===================================

    
    dose = (
        _parse_dose_amount(
            single_dose
        )
    )

    daily_count = (
        _parse_daily_count(
            daily_frequency
        )
    )

    if (
        dose is not None
        and daily_count
    ):
        daily_value = (
            dose["value"]
            * daily_count
        )

        if (
            float(
                daily_value
            ).is_integer()
         ):
            daily_value = int(
                daily_value
            )

        calculated_daily_dose_text = (
            f"1일 {daily_value}"
            f"{dose['unit']} "
            "(용법 기준 계산)"
        )

    return {
        "success": True,

        "itemSeq":
            item_seq,

        "medicineName":
            medicine_name,

        "dosageForm":
            article.get(
                "title",
                "",
            ),

        "singleDoseText":
            single_dose,

        "dailyFrequencyText":
            daily_frequency,

        "calculatedDailyDoseText":
            calculated_daily_dose_text,

        "maxDailyDoseText":
            max_daily_text,

        "maxDailyDoseSource":
            max_daily_source,

        "usageParagraphs":
            paragraphs,

        "usage":
            usage,
    }