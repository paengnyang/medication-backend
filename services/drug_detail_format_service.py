import html
import re


# ============================================================
# 공통
# ============================================================

def _safe_text(value):
    if value is None:
        return ""

    if isinstance(value, list):
        return " ".join(
            str(item)
            for item in value
            if item is not None
        ).strip()

    return str(value).strip()


def _clean_document_text(text):
    text = _safe_text(text)

    if not text:
        return ""

    text = html.unescape(text)

    # 이스케이프된 XML 태그
    text = (
        text
        .replace(r"\<", "<")
        .replace(r"\>", ">")
    )

    # CDATA
    text = (
        text
        .replace("<![CDATA[", "")
        .replace("]]>", "")
    )

    # XML / HTML 태그 제거
    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    # 공백 정리
    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# ARTICLE / PARAGRAPH 추출
# ============================================================

def _extract_articles(document):
    """
    식약처 XML의

    <ARTICLE title="...">
        <PARAGRAPH>...</PARAGRAPH>
    </ARTICLE>

    구조를 파싱한다.
    """

    document = _safe_text(document)

    if not document:
        return []

    document = html.unescape(document)

    document = (
        document
        .replace(r"\<", "<")
        .replace(r"\>", ">")
    )

    article_pattern = re.compile(
        r'<ARTICLE[^>]*'
        r'title="([^"]*)"'
        r'[^>]*>'
        r'(.*?)'
        r'</ARTICLE>',
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    paragraph_pattern = re.compile(
        r'<PARAGRAPH[^>]*>'
        r'(.*?)'
        r'</PARAGRAPH>',
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    articles = []

    for article_match in (
        article_pattern.finditer(
            document
        )
    ):
        title = (
            _clean_document_text(
                article_match.group(1)
            )
        )

        body = (
            article_match.group(2)
            or ""
        )

        paragraphs = []

        for paragraph_match in (
            paragraph_pattern.finditer(
                body
            )
        ):
            paragraph = (
                _clean_document_text(
                    paragraph_match.group(1)
                )
            )

            if paragraph:
                paragraphs.append(
                    paragraph
                )

        # PARAGRAPH 태그가 없는
        # 일부 식약처 데이터 fallback
        if not paragraphs:
            cdata_list = re.findall(
                r'<!\[CDATA\[(.*?)\]\]>',
                body,
                flags=re.DOTALL,
            )

            for value in cdata_list:
                cleaned = (
                    _clean_document_text(
                        value
                    )
                )

                if cleaned:
                    paragraphs.append(
                        cleaned
                    )

        articles.append(
            {
                "title": title,
                "paragraphs":
                    paragraphs,
            }
        )

    return articles


# ============================================================
# 번호 제거
# ============================================================

def _remove_number_prefix(text):
    text = _safe_text(text)

    # 2.
    text = re.sub(
        r'^\s*\d+\.\s*',
        '',
        text,
    )

    # 1)
    text = re.sub(
        r'^\s*\d+\)\s*',
        '',
        text,
    )

    # - / •
    text = re.sub(
        r'^\s*[-•·]\s*',
        '',
        text,
    )

    return text.strip()


# ============================================================
# 금기 ARTICLE 판단
# ============================================================

def _is_contraindication_article(
    title,
):
    title = (
        _clean_document_text(
            title
        )
    )

    if not title:
        return False

    # "신중히 투여"는 금기가 아님
    if (
        "신중히" in title
        or "주의" in title
    ):
        return False

    # 병용금지 ARTICLE은
    # 환자 금기사항과 분리
    interaction_words = [
        "다음의 약물",
        "다음 약물",
        "함께 복용",
        "병용",
        "약과 함께",
    ]

    if any(
        word in title
        for word
        in interaction_words
    ):
        return False

    if "금기" in title:
        return True

    prohibition_words = [
        "복용하지 말",
        "투여하지 말",
        "사용하지 말",
        "복용하지 마",
        "투여하지 마",
        "사용하지 마",
    ]

    has_prohibition = any(
        word in title
        for word
        in prohibition_words
    )

    subject_words = [
        "환자",
        "사람",
        "경우",
        "대상",
    ]

    has_subject = any(
        word in title
        for word
        in subject_words
    )

    return (
        has_prohibition
        and has_subject
    )


# ============================================================
# 금기사항의 공통 머리말 제거
# ============================================================

def _clean_contraindication_item(
    text,
):
    text = (
        _clean_document_text(
            text
        )
    )

    text = (
        _remove_number_prefix(
            text
        )
    )

    # 예:
    # 다음과 같은 사람은 이 약을 복용하지 말 것
    text = re.sub(
        r'^다음\s*'
        r'(과 같은\s*)?'
        r'(환자|사람|경우|대상)'
        r'(에게는|에는|은|는)?'
        r'\s*'
        r'(이 약을\s*)?'
        r'(복용|투여|사용)하지\s*'
        r'(말\s*것|마십시오|마세요)'
        r'\s*[:\-–—.]?\s*',
        '',
        text,
    )

    # 끝에 반복적으로 붙는
    # "이 약을 복용하지 마십시오" 제거
    text = re.sub(
        r'\s*'
        r'(은|는|에게는)?'
        r'\s*'
        r'(이 약을\s*)?'
        r'(복용|투여|사용)하지\s*'
        r'(말\s*것|마십시오|마세요)'
        r'\.?\s*$',
        '',
        text,
    )

    return text.strip()


def _is_generic_contraindication_text(
    text,
):
    normalized = re.sub(
        r'\s+',
        '',
        _safe_text(text),
    )

    generic_patterns = [
        "다음환자에는투여하지말것",
        "다음환자에는복용하지말것",
        "다음과같은사람은이약을복용하지말것",
        "다음과같은사람",
        "다음환자",
        "다음과같은환자",
    ]

    if normalized in generic_patterns:
        return True

    # 제목 역할만 하는 짧은 문구
    if (
        normalized.startswith(
            "다음과같은사람"
        )
        and len(normalized) < 25
    ):
        return True

    return False


# ============================================================
# 금기사항 추출
# ============================================================

def _extract_contraindications(
    warning_document,
):
    articles = (
        _extract_articles(
            warning_document
        )
    )

    result = []

    for article in articles:

        title = article.get(
            "title",
            "",
        )

        if not (
            _is_contraindication_article(
                title
            )
        ):
            continue

        paragraphs = article.get(
            "paragraphs",
            [],
        )

        for paragraph in paragraphs:

            text = (
                _clean_document_text(
                    paragraph
                )
            )

            if not text:
                continue

            # 한 PARAGRAPH 안에
            # 1) ... 2) ... 3) ...
            # 가 들어있는 경우만 분리
            text = re.sub(
                r'\s+(?=\d+\)\s*)',
                '\n',
                text,
            )

            pieces = (
                text.split("\n")
            )

            for piece in pieces:

                cleaned = (
                    _clean_contraindication_item(
                        piece
                    )
                )

                if not cleaned:
                    continue

                if (
                    _is_generic_contraindication_text(
                        cleaned
                    )
                ):
                    continue

                # 쉼표 기준으로는
                # 절대 자르지 않는다.
                if cleaned not in result:
                    result.append(
                        cleaned
                    )

    return result


# ============================================================
# 용법·용량
# ============================================================

def _select_usage_article(
    drug,
    articles,
):
    if not articles:
        return None

    if len(articles) == 1:
        return articles[0]

    raw = (
        drug.get("raw")
        if isinstance(
            drug.get("raw"),
            dict,
        )
        else {}
    )

    item_name = (
        _safe_text(
            drug.get("itemName")
            or raw.get("ITEM_NAME")
        )
    )

    form_name = (
        _safe_text(
            raw.get(
                "FORM_CODE_NAME"
            )
        )
    )

    combined = (
        f"{item_name} {form_name}"
    )

    keywords = []

    if (
        "시럽" in combined
        or "현탁액" in combined
    ):
        keywords = [
            "시럽",
            "현탁",
            "액제",
        ]

    elif (
        "캡슐" in combined
        or "캅셀" in combined
    ):
        keywords = [
            "캡슐",
            "캅셀",
        ]

    elif "정" in combined:
        keywords = [
            "정제",
            "정",
        ]

    elif "산" in combined:
        keywords = [
            "산제",
            "과립",
        ]

    for keyword in keywords:

        for article in articles:

            title = (
                _safe_text(
                    article.get(
                        "title"
                    )
                )
            )

            if keyword in title:
                return article

    # 제형을 못 찾으면
    # 첫 ARTICLE 사용
    return articles[0]


def _extract_dosage_summary(
    drug,
):
    raw = (
        drug.get("raw")
        if isinstance(
            drug.get("raw"),
            dict,
        )
        else {}
    )

    usage = (
        _safe_text(
            drug.get("usage")
            or raw.get(
                "UD_DOC_DATA"
            )
        )
    )

    if not usage:
        return []

    articles = (
        _extract_articles(
            usage
        )
    )

    # XML ARTICLE 구조가 없는 경우
    if not articles:
        cleaned = (
            _clean_document_text(
                usage
            )
        )

        if not cleaned:
            return []

        sentences = re.split(
            r'(?<=[.!?])\s+',
            cleaned,
        )

        result = [
            sentence.strip()
            for sentence in sentences
            if sentence.strip()
        ]

        return result[:4]

    article = (
        _select_usage_article(
            drug,
            articles,
        )
    )

    if not article:
        return []

    paragraphs = (
        article.get(
            "paragraphs",
            []
        )
    )

    result = []

    for paragraph in paragraphs:

        text = (
            _remove_number_prefix(
                paragraph
            )
        )

        if not text:
            continue

        if text not in result:
            result.append(
                text
            )

    # 화면이 지나치게 길어지는 것을
    # 막기 위해 최대 4개
    return result[:4]


# ============================================================
# 핵심 주의사항
# ============================================================

def _build_precaution_summary(
    warning_document,
):
    text = (
        _clean_document_text(
            warning_document
        )
    )

    if not text:
        return []

    result = []

    def add(value):
        if (
            value
            and value not in result
        ):
            result.append(value)

    # 다른 약 병용
    interaction_words = [
        "감기약",
        "해열진통제",
        "항히스타민",
        "진해거담제",
    ]

    if any(
        word in text
        for word
        in interaction_words
    ):
        add(
            "다른 감기약·해열진통제 등과 "
            "함께 복용할 때 주의하세요."
        )

    # 임신 / 기저질환
    risk_words = [
        "임부",
        "임신",
        "고혈압",
        "심장질환",
        "간장질환",
        "신장질환",
        "신기능",
        "녹내장",
        "당뇨병",
    ]

    if any(
        word in text
        for word
        in risk_words
    ):
        add(
            "임신 중이거나 기저질환이 있다면 "
            "복용 전 의사·약사와 상담하세요."
        )

    # 졸음 / 운전
    if (
        "졸음" in text
        and (
            "운전" in text
            or "기계조작" in text
            or "기계 조작" in text
        )
    ):
        add(
            "복용 후 졸음이 올 수 있어 "
            "운전·기계 조작에 주의하세요."
        )

    # 음주
    if (
        "음주" in text
        or "알코올" in text
    ):
        add(
            "복용 중에는 음주를 피하세요."
        )

    return result[:4]

def _build_precaution_details(
    warning_document,
):
    """
    식약처 NB_DOC_DATA를

    [
        {
            "title": "1. 경고",
            "items": [
                "...",
                "..."
            ]
        },
        ...
    ]

    형태로 정리한다.

    금기사항은 별도 contraindications에서
    표시하므로 여기서는 제외한다.
    """

    articles = (
        _extract_articles(
            warning_document
        )
    )

    result = []

    for article in articles:

        title = (
            _clean_document_text(
                article.get(
                    "title",
                    "",
                )
            )
        )

        if not title:
            continue

        # 금기사항은 이미
        # 빨간 박스에서 따로 표시
        if _is_contraindication_article(
            title
        ):
            continue

        items = []

        for paragraph in article.get(
            "paragraphs",
            [],
        ):
            text = (
                _clean_document_text(
                    paragraph
                )
            )

            text = (
                _remove_number_prefix(
                    text
                )
            )

            if not text:
                continue

            if text not in items:
                items.append(
                    text
                )

        if not items:
            continue

        result.append(
            {
                "title": title,
                "items": items,
            }
        )

    return result

# ============================================================
# 최종 상세정보 보강
# ============================================================

def enrich_drug_detail(
    drug,
):
    """
    기존 상세정보는 그대로 유지하고

    dosageSummary
    precautionSummary
    contraindications

    만 추가/정리한다.
    """

    if not isinstance(
        drug,
        dict,
    ):
        return drug

    result = dict(drug)

    raw = (
        result.get("raw")
        if isinstance(
            result.get("raw"),
            dict,
        )
        else {}
    )

    usage = (
        _safe_text(
            result.get("usage")
            or raw.get(
                "UD_DOC_DATA"
            )
        )
    )

    warning_document = (
        _safe_text(
            result.get(
                "precautions"
            )
            or result.get(
                "warning"
            )
            or result.get(
                "nbDocData"
            )
            or raw.get(
                "NB_DOC_DATA"
            )
        )
    )

    result[
        "dosageSummary"
    ] = (
        _extract_dosage_summary(
            result
        )
    )

    result[
        "precautionSummary"
    ] = (
        _build_precaution_summary(
            warning_document
        )
    )

    result[
        "contraindications"
    ] = (
        _extract_contraindications(
            warning_document
        )
    )

    result[
        "precautionDetails"
    ] = (
        _build_precaution_details(
            warning_document
        )
    )

    # 원문은 삭제하지 않는다.
    if usage:
        result["usage"] = usage

    if warning_document:
    
    # 화면에서 사용할
    # 태그 제거된 일반 텍스트
        result[
            "precautions"
        ] = (
            _clean_document_text(
                warning_document
            )
        )

    return result