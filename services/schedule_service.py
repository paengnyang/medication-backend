# 하루 복용 횟수에 따라 기본 복약 시간을 생성하는 함수
def generate_times(daily_count: int):
    base_times = {
        1: ["08:30"],
        2: ["08:30", "19:30"],
        3: ["08:30", "13:30", "19:30"],
        4: ["08:30", "13:30", "18:30", "22:00"]
    }

    return base_times.get(daily_count, ["08:30"])