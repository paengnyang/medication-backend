import re


def parse_prescription_text(text: str):
    """
    OCR 텍스트에서 약 이름, 하루 복용 횟수, 복용 기간, 복용량, 복용 시점을 간단히 추출한다.
    """

    medicines = []

    # 줄 단위로 분리
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    for line in lines:
        # 기본값
        medicine_name = None
        daily_count = None
        period = None
        dosage = 1
        timing = "정보 없음"

        # 1일 3회 추출
        daily_match = re.search(r"1일\s*(\d+)회", line)
        if daily_match:
            daily_count = int(daily_match.group(1))

        # 7일분 추출
        period_match = re.search(r"(\d+)일분", line)
        if period_match:
            period = int(period_match.group(1))

        # 1회 1정 추출
        dosage_match = re.search(r"1회\s*(\d+)", line)
        if dosage_match:
            dosage = int(dosage_match.group(1))

        # 식후/식전/취침 전 추출
        if "식후" in line:
            timing = "식후"
        elif "식전" in line:
            timing = "식전"
        elif "취침" in line:
            timing = "취침 전"

        # 약 이름 추정
        # 가장 단순하게는 줄의 첫 번째 단어를 약 이름으로 처리
        words = line.split()
        if len(words) > 0:
            medicine_name = words[0]

        # 복용 횟수나 기간이 있는 줄만 약 정보로 판단
        if medicine_name and (daily_count or period):
            if daily_count is None:
                daily_count = 1

            if period is None:
                period = 1

            total_count = daily_count * period * dosage

            medicines.append({
                "medicineName": medicine_name,
                "dailyCount": daily_count,
                "dosage": dosage,
                "timing": timing,
                "period": period,
                "totalCount": total_count
            })

    return medicines