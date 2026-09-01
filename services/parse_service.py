import re


def parse_prescription_text(text: str):
    medicines = []

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # 기본값
    dosage = 1
    daily_count = 1
    period = 1
    timing = "정보 없음"

    # 1. 투약량 / 투여횟수 / 투약일수 표 찾기
    # 예: 05 | 3 | 4
    for line in lines:
        clean_line = line.replace(" ", "")

        # 05 | 3 | 4 / 0.5 | 3 | 4 / 1 | 3 | 4 형태 처리
        table_match = re.search(r"([0-9.]+)\|([0-9]+)\|([0-9]+)", clean_line)

        if table_match:
            raw_dosage = table_match.group(1)
            daily_count = int(table_match.group(2))
            period = int(table_match.group(3))

            # OCR에서 0.5가 05로 읽히는 경우 보정
            if raw_dosage == "05":
                dosage = 0.5
            else:
                dosage = float(raw_dosage)

            break

    # 2. 약 이름 추출
    for line in lines:
        # 예:
        # 650204010 메드닌정(메틸프레드니솔론) (4070/1정)
        # 642506080 크라모틴정(아목시실린:클라불란산칼륨(2:1))_(1정)

        medicine_match = re.search(
            r"^\d{8,9}\s+([가-힣A-Za-z0-9]+(?:정|캡슐|시럽|액|주|연고|크림))",
            line
        )

        if medicine_match:
            medicine_name = medicine_match.group(1)

            total_count = dosage * daily_count * period

            medicines.append({
                "medicineName": medicine_name,
                "dailyCount": daily_count,
                "dosage": dosage,
                "timing": timing,
                "period": period,
                "totalCount": total_count
            })

    return medicines