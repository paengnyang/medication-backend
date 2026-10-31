import re

import cv2
import numpy as np
import pytesseract


# ============================================================
# Windows Tesseract 경로
# ============================================================

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)


# ============================================================
# OCR 결과 평가용 키워드
# ============================================================

PRESCRIPTION_KEYWORDS = [
    "약품",
    "약품명",
    "복약",
    "복약안내",
    "처방",
    "처방전",
    "조제",
    "투약",
    "투여",
    "투약량",
    "투여량",
    "횟수",
    "일수",
    "복용",
    "식전",
    "식후",
    "취침",
    "환자",
    "병원",
    "약국",
]


# ============================================================
# 이미지 확대
# ============================================================

def _resize_for_ocr(image):
    """
    작은 글씨 인식을 위해 이미지 확대.

    너무 작은 사진은 약 2~3배 확대하고
    이미 큰 이미지는 불필요하게 확대하지 않는다.
    """

    height, width = image.shape[:2]

    target_width = 2000

    if width >= target_width:
        return image

    scale = target_width / width

    # 지나친 확대 방지
    scale = min(scale, 3.0)

    resized = cv2.resize(
        image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    return resized


# ============================================================
# 이미지 회전
# ============================================================

def _rotate_image(image, angle):
    if angle == 0:
        return image

    if angle == 90:
        return cv2.rotate(
            image,
            cv2.ROTATE_90_CLOCKWISE,
        )

    if angle == 180:
        return cv2.rotate(
            image,
            cv2.ROTATE_180,
        )

    if angle == 270:
        return cv2.rotate(
            image,
            cv2.ROTATE_90_COUNTERCLOCKWISE,
        )

    return image


# ============================================================
# 기본 grayscale + 대비 강화
# ============================================================

def _make_gray(image):
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    # 작은 글씨 대비 강화
    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )

    gray = clahe.apply(gray)

    return gray


# ============================================================
# 여러 전처리 이미지 생성
# ============================================================

def _make_preprocessing_variants(image):
    """
    한 가지 전처리만 사용하지 않고 여러 방식 생성.

    처방전마다 종이색/밝기/표/사진 품질이 다르기 때문에
    가장 잘 인식되는 결과를 나중에 선택한다.
    """

    gray = _make_gray(image)

    variants = {
        "gray": gray,
    }

    # --------------------------------
    # Otsu 이진화
    # --------------------------------

    blurred = cv2.GaussianBlur(
        gray,
        (3, 3),
        0,
    )

    _, otsu = cv2.threshold(
        blurred,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    variants["otsu"] = otsu

    # --------------------------------
    # Adaptive threshold
    # 조명 차이가 있는 사진에 유리
    # --------------------------------

    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )

    variants["adaptive"] = adaptive

    return variants


# ============================================================
# 실제 Tesseract 실행
# ============================================================

def _run_tesseract(image, psm):
    config = (
        f"--oem 3 "
        f"--psm {psm} "
        f"-c preserve_interword_spaces=1"
    )

    text = pytesseract.image_to_string(
        image,
        lang="kor+eng",
        config=config,
    )

    return text.strip()


# 여러 OCR 방식에서 같은 이미지 위치의 약 이름을 비교한다.
OCR_MEDICINE_PATTERN = re.compile(
    r"[가-힣A-Za-z0-9·\-.]+"
    r"(?:연질캡슐|캡슐|캡슬|서방정|시럽|정)"
    r"(?:\d+(?:\.\d+)?(?:mg|g|ml)?)?"
    r"|[가-힣]{2,}\d+시간(?:이알서방정|이알서방|이알서|서방정)",
    re.IGNORECASE,
)

OCR_NON_MEDICINE = {
    "환자정", "원형정", "필름코팅정",
    "환자정보",
    "코팅정", "장방형정", "규정",
    "위정", "우정", "민정",
    "1정", "2정", "3정",
}


def _positioned_medicine_names(image, psm, source):
    """
    Tesseract 단어 좌표를 이용해 약 이름 후보 추출.
    동일한 이미지 영역에 있는 OCR 결과를 비교하기 위한 함수.
    """
    config = (
        f"--oem 3 --psm {psm} "
        "-c preserve_interword_spaces=1"
    )

    data = pytesseract.image_to_data(
        image,
        lang="kor+eng",
        config=config,
        output_type=pytesseract.Output.DICT,
    )

    lines = {}

    for i, raw_word in enumerate(data["text"]):
        word = str(raw_word or "").strip()
        if not word:
            continue

        key = (
            data["block_num"][i],
            data["par_num"][i],
            data["line_num"][i],
        )

        try:
            confidence = float(data["conf"][i])
        except (ValueError, TypeError):
            confidence = -1

        lines.setdefault(key, []).append({
            "text": word,
            "x": int(data["left"][i]),
            "y": int(data["top"][i]),
            "w": int(data["width"][i]),
            "h": int(data["height"][i]),
            "conf": confidence,
        })

    found = []

    for words in lines.values():
        words.sort(key=lambda w: w["x"])

        joined = ""
        ranges = []
        previous_right = None

        for word in words:
            # OCR 단어 사이의 실제 간격 확인
            if previous_right is not None:
                gap = word["x"] - previous_right

                # 글자 사이가 충분히 떨어졌으면
                # 서로 다른 단어로 구분한다.
                if gap > max(8, word["h"] * 0.5):
                    joined += " "
            start = len(joined)

            joined += word["text"]

            ranges.append((
                start,
                len(joined),
                word,
            ))  

            previous_right = (
                word["x"] + word["w"]
            )
        for match in OCR_MEDICINE_PATTERN.finditer(joined):
            name = match.group().strip(".,·-")

            if len(name) < 3 or len(name) > 45:
                continue

            if name in OCR_NON_MEDICINE:
                continue

            if re.fullmatch(r"\d+(?:정|캡슐)", name):
                continue

            matching_words = [
                word
                for start, end, word in ranges
                if start < match.end()
                and end > match.start()
            ]

            if not matching_words:
                continue

            x = min(w["x"] for w in matching_words)
            y = min(w["y"] for w in matching_words)
            right = max(
                w["x"] + w["w"]
                for w in matching_words
            )
            bottom = max(
                w["y"] + w["h"]
                for w in matching_words
            )

            confidences = [
                w["conf"]
                for w in matching_words
                if w["conf"] >= 0
            ]

            found.append({
                "name": name,
                "x": x,
                "y": y,
                "width": right - x,
                "height": bottom - y,
                "confidence": round(
                    sum(confidences) / len(confidences), 1
                ) if confidences else 0.0,
                "source": source,
            })

    return found


def _group_ocr_medicine_names(candidates):
    """
    서로 다른 OCR 설정이 동일한 위치에서 읽은
    약 이름을 같은 그룹으로 묶는다.

    다른 그룹끼리는 약 이름이 비슷하더라도
    자동으로 하나로 합치지 않는다.
    """
    groups = []

    for candidate in sorted(
        candidates,
        key=lambda c: (c["y"], c["x"]),
    ):
        cy = candidate["y"] + candidate["height"] / 2

        closest = None
        closest_distance = float("inf")

        for group in groups:
            gx = group["x"]
            gy = group["y"]

            distance_x = abs(candidate["x"] - gx)
            distance_y = abs(cy - gy)

            tolerance_x = max(
                45, candidate["height"] * 3
            )
            tolerance_y = max(
                16, candidate["height"] * 1.4
            )

            if (
                distance_x <= tolerance_x
                and distance_y <= tolerance_y
            ):
                distance = (
                    distance_x / tolerance_x
                    + distance_y / tolerance_y
                )

                if distance < closest_distance:
                    closest = group
                    closest_distance = distance

        if closest is None:
            groups.append({
                "x": candidate["x"],
                "y": cy,
                "candidates": [candidate],
            })
        else:
            closest["candidates"].append(candidate)

            # 좌표를 평균으로 갱신
            items = closest["candidates"]
            closest["x"] = sum(
                c["x"] for c in items
            ) / len(items)
            closest["y"] = sum(
                c["y"] + c["height"] / 2
                for c in items
            ) / len(items)

    results = []

    for group in groups:
        items = group["candidates"]

        sources = {
            c["source"] for c in items
        }

        # 서로 다른 설정 2개 이상에서 보이는 후보만 사용
        if len(sources) < 2:
            continue

        unique_names = list(dict.fromkeys(
            c["name"] for c in items
        ))

        results.append({
            "names": unique_names,
            "sourceCount": len(sources),
            "confidence": round(
                max(c["confidence"] for c in items), 1
            ),
            "x": round(group["x"]),
            "y": round(group["y"]),
        })

    return results


# ============================================================
# OCR 결과 품질 평가
# ============================================================


def _score_text(text):
    """
    OCR 결과 품질 평가.

    단순히 글자가 많은 결과보다
    한글 단어와 약 이름이 잘 인식된 결과를 우선한다.
    """
    if not text or not text.strip():
        return -999999

    score = 0.0

    korean = re.findall(r"[가-힣]", text)
    english = re.findall(r"[A-Za-z]", text)
    digits = re.findall(r"\d", text)

    total_letters = (
        len(korean)
        + len(english)
        + len(digits)
    )

    if total_letters == 0:
        return -999999

    korean_ratio = len(korean) / total_letters
    english_ratio = len(english) / total_letters

    # 1. 한글 비율을 가장 중요한 기준으로 사용
    score += korean_ratio * 120

    # 2. 영문이 지나치게 많으면 감점
    # 영어 의약품 설명이 일부 포함될 수 있으므로
    # 모든 영어를 제거하지는 않는다.
    if english_ratio > 0.5:
        score -= (english_ratio - 0.5) * 120

    # 3. 의미 있는 한글 단어 수
    korean_words = re.findall(
        r"[가-힣]{2,}",
        text,
    )
    score += min(len(korean_words), 35) * 2

    # 4. 처방전 관련 핵심 단어
    for keyword in PRESCRIPTION_KEYWORDS:
        if keyword in text:
            score += 8

    # 5. 약 이름처럼 보이는 후보
    medicine_patterns = re.findall(
        r"[가-힣A-Za-z0-9·\-]+"
        r"(?:연질캡슐|캡슐|시럽|현탁액|"
        r"연고|크림|과립|정)",
        text,
    )

    # 약 이름으로 보이는 후보가 지나치게 많다고
    # 무한정 높은 점수를 주지 않는다.
    score += min(len(medicine_patterns), 12) * 8

    # 6. 너무 짧은 결과는 감점
    if len(korean) < 15:
        score -= 30

    # 7. 기호만 있는 줄은 잡음으로 취급
    noise_lines = 0

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        useful_chars = re.findall(
            r"[가-힣A-Za-z0-9]",
            line,
        )

        if len(useful_chars) == 0:
            noise_lines += 1

    score -= min(noise_lines, 40) * 2

    return round(score, 2)


# ============================================================
# 가장 올바른 방향 찾기
# ============================================================

def _find_best_rotation(image):
    """
    0 / 90 / 180 / 270도를 각각 OCR한 뒤
    가장 한국어 처방전답게 읽힌 방향을 선택.
    """

    best_image = image
    best_angle = 0
    best_score = -999999
    best_preview_text = ""

    for angle in [
        0,
        90,
        180,
        270,
    ]:
        rotated = _rotate_image(
            image,
            angle,
        )

        gray = _make_gray(
            rotated
        )

        try:
            text = _run_tesseract(
                gray,
                psm=11,
            )

            score = _score_text(
                text
            )

            print(
                f"[OCR ROTATION] "
                f"angle={angle}, "
                f"score={score}"
            )

            if score > best_score:
                best_score = score
                best_angle = angle
                best_image = rotated
                best_preview_text = text

        except Exception as e:
            print(
                f"[OCR ROTATION ERROR] "
                f"angle={angle}: {e}"
            )

    print(
        f"[OCR] 선택된 회전 각도: "
        f"{best_angle}도"
    )

    return (
        best_image,
        best_angle,
        best_preview_text,
    )


# ============================================================
# 최종 OCR 함수
# ============================================================

def extract_text_from_image(
    file_bytes: bytes,
    include_name_candidates=False,
):
    """
    업로드된 처방전 이미지에서
    최대한 안정적으로 OCR 텍스트를 추출한다.

    처리 과정:

    1. 이미지 decoding
    2. 크기 확대
    3. 0/90/180/270 회전 비교
    4. gray / Otsu / adaptive threshold 생성
    5. PSM 4 / 6 / 11 비교
    6. 가장 높은 품질의 결과 반환
    """

    # --------------------------------
    # 1. bytes → numpy
    # --------------------------------

    np_arr = np.frombuffer(
        file_bytes,
        np.uint8,
    )

    # --------------------------------
    # 2. 이미지 decode
    # --------------------------------

    image = cv2.imdecode(
        np_arr,
        cv2.IMREAD_COLOR,
    )

    if image is None:
        raise ValueError(
            "이미지를 읽을 수 없습니다."
        )

    print(
        "[OCR] 원본 이미지 크기:",
        image.shape,
    )

    # --------------------------------
    # 3. 확대
    # --------------------------------

    image = _resize_for_ocr(
        image
    )

    print(
        "[OCR] 확대 후 이미지 크기:",
        image.shape,
    )

    # --------------------------------
    # 4. 가장 올바른 회전 방향 선택
    # --------------------------------

    best_rotated_image, angle, rotation_text = (
        _find_best_rotation(
            image
        )
    )

    # --------------------------------
    # 5. 여러 preprocessing 생성
    # --------------------------------

    variants = (
        _make_preprocessing_variants(
            best_rotated_image
        )
    )

    # --------------------------------
    # 6. 여러 PSM 조합 실행
    #
    # PSM 4:
    #   column / 표가 있는 문서
    #
    # PSM 6:
    #   한 덩어리의 텍스트
    #
    # PSM 11:
    #   흩어진 텍스트
    # --------------------------------

    psm_modes = [
        4,
        6,
        11,
    ]

    candidates = []

    # 회전 판단 때 얻은 결과도 후보에 포함
    if rotation_text:
        candidates.append({
            "name": "rotation-preview",
            "psm": 11,
            "text": rotation_text,
            "score": _score_text(
                rotation_text
            ),
        })

    for variant_name, processed_image in variants.items():

        for psm in psm_modes:

            try:
                text = _run_tesseract(
                    processed_image,
                    psm,
                )

                score = _score_text(
                    text
                )

                print(
                    "[OCR CANDIDATE]",
                    f"variant={variant_name}",
                    f"psm={psm}",
                    f"score={score}",
                )

                candidates.append({
                    "name":
                        variant_name,

                    "psm":
                        psm,

                    "text":
                        text,

                    "score":
                        score,
                })

            except Exception as e:

                print(
                    "[OCR ERROR]",
                    f"variant={variant_name}",
                    f"psm={psm}",
                    str(e),
                )

    # --------------------------------
    # 7. 결과가 하나도 없는 경우
    # --------------------------------

    if not candidates:
        return ""

    # --------------------------------
    # 8. 최고 점수 OCR 결과 선택
    # --------------------------------

    # 어떤 OCR 결과가 가장 좋은지 비교
    ranked_candidates = sorted(
        candidates,
        key=lambda item: item["score"],
        reverse=True,
    )

    print("========== OCR TOP 3 ==========")

    for index, candidate in enumerate(
        ranked_candidates[:3],
        start=1,
    ):
        print(
            f"[OCR #{index}]",
            "variant=", candidate["name"],
            "psm=", candidate["psm"],
            "score=", candidate["score"],
        )

        # 비교하기 쉽도록 앞부분만 출력
        print(candidate["text"][:600])
        print("------------------------------")

    print("================================")
    # OCR 방식별 약 이름 후보 확인
    medicine_pattern = re.compile(
        r"[가-힣A-Za-z0-9·\-]+"
        r"(?:연질캡슐|캡슐|시럽|서방정|정)"
    )

    print("========== OCR 약 이름 후보 비교 ==========")

    for candidate in candidates:
        text = candidate["text"]

        found_names = list(dict.fromkeys(
            medicine_pattern.findall(text)
        ))

        print(
            f"[OCR MEDICINES] "
            f"variant={candidate['name']} "
            f"psm={candidate['psm']} "
            f"score={candidate['score']}"
        )

        print("후보:", found_names)

    print("========================================")
    #best = max(
       #  key=lambda item:
     #       item["score"],
    #)
    # OCR 테스트용: gray + PSM 4 우선 선택
    best = next(
        (
            candidate
            for candidate in candidates
            if candidate["name"] == "gray"
            and candidate["psm"] == 4
        ),
        max(
            candidates,
            key=lambda item: item["score"],
        ),
    )
    print(
        "========== OCR BEST =========="
    )

    print(
        "rotation:",
        angle,
    )

    print(
        "variant:",
        best["name"],
    )

    print(
        "psm:",
        best["psm"],
    )

    print(
        "score:",
        best["score"],
    )

    print(
        "================================"
    )

    print(
        best["text"]
    )

    print(
        "================================"
    )

    
    best_text = best["text"].strip()

    if not include_name_candidates:
        return best_text

    # 한 가지 OCR 방식에 의존하지 않고
    # 약 이름을 잘 읽었던 여러 설정을 비교한다.
    selected_settings = [
        ("gray", 4),
        ("gray", 6),
        ("gray", 11),
        ("otsu", 4),
        ("otsu", 6),
        ("adaptive", 6),
        ("adaptive", 11),
    ]

    positioned_candidates = []

    for variant_name, psm in selected_settings:
        try:
            processed = variants[variant_name]

            found = _positioned_medicine_names(
                processed,
                psm,
                f"{variant_name}-psm{psm}",
            )

            positioned_candidates.extend(found)

        except Exception as e:
            print(
                "[OCR POSITION ERROR]",
                variant_name,
                psm,
                str(e),
            )

    name_groups = _group_ocr_medicine_names(
        positioned_candidates
    )

    print("========== OCR 위치별 후보 ==========")

    for group in name_groups:
        print(
            "[OCR GROUP]",
            group["names"],
            "sources=", group["sourceCount"],
            "position=", (group["x"], group["y"]),
        )

    print("===================================")

    return {
        "text": best_text,
        "nameGroups": name_groups,
    }
