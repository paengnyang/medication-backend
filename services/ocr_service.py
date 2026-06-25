import cv2
import numpy as np
import pytesseract
from PIL import Image


# Windows에서 Tesseract 경로 설정
# 본인 PC의 설치 경로가 다르면 수정해야 함
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def extract_text_from_image(file_bytes: bytes) -> str:
    """
    업로드된 이미지 파일 bytes를 받아 OCR 텍스트를 추출하는 함수
    """

    # bytes 데이터를 numpy 배열로 변환
    np_arr = np.frombuffer(file_bytes, np.uint8)

    # OpenCV 이미지로 디코딩
    image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("이미지를 읽을 수 없습니다.")

    # 그레이스케일 변환
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # 노이즈 감소
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # 이진화
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # OCR 실행
    # kor+eng: 한국어와 영어 인식
    text = pytesseract.image_to_string(thresh, lang="kor+eng")

    return text.strip()