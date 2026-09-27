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


# ============================================================
# OCR 결과 품질 평가
# ============================================================

def _score_text(text):
    """
    OCR 후보들 중
    '한국어 처방전답게 읽힌 결과'에 높은 점수를 준다.
    """

    if not text:
        return -999999

    score = 0

    # --------------------------------
    # 1. 한글이 많이 살아 있을수록 가점
    # --------------------------------

    korean_chars = re.findall(
        r"[가-힣]",
        text,
    )

    score += len(korean_chars) * 2

    # --------------------------------
    # 2. 처방 관련 단어 가점
    # --------------------------------

    for keyword in PRESCRIPTION_KEYWORDS:
        count = text.count(keyword)
        score += count * 20

    # --------------------------------
    # 3. 약 이름처럼 보이는 표현 가점
    # --------------------------------

    medicine_patterns = re.findall(
        r"[가-힣A-Za-z0-9·\-\(\)]+"
        r"(?:연질캡슐|캡슐|시럽|현탁액|"
        r"연고|크림|과립|정|액|산|주)",
        text,
    )

    score += len(medicine_patterns) * 15

    # --------------------------------
    # 4. 숫자도 어느 정도 있어야 처방전 가능성 증가
    # --------------------------------

    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        text,
    )

    score += min(
        len(numbers),
        30,
    )

    # --------------------------------
    # 5. 의미 있는 줄 개수
    # --------------------------------

    lines = [
        line.strip()
        for line in text.splitlines()
        if len(line.strip()) >= 2
    ]

    score += min(
        len(lines),
        30,
    )

    # --------------------------------
    # 6. 한글이 거의 없고 영문만 비정상적으로 많은 경우 감점
    # --------------------------------

    english_chars = re.findall(
        r"[A-Za-z]",
        text,
    )

    if (
        len(english_chars) > 50
        and len(korean_chars) < 10
    ):
        score -= 100

    return score


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
    file_bytes: bytes
) -> str:
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

    best = max(
        candidates,
        key=lambda item:
            item["score"],
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

    return best["text"].strip()