
from services.market_performance import (
    get_market_performance,
)


def _comparable_record(result):
    """
    한 제품에 생산/수입 실적이 동시에 있으면
    서로 다른 단위를 임의로 합치지 않는다.
    """
    if result.get("status") != "found":
        return None

    records = result.get("records", [])

    if len(records) != 1:
        return None

    return records[0]


def rank_with_market_data(
    recommendations,
    relevance_field=None,
    max_lookups=10,
):
    """
    기존 추천 결과만 재정렬.

    relevance_field:
        기존 증상 적합성 점수 필드명.
        None이면 기존 순위를 유지하면서
        인접 후보 간 제한적으로 정렬.
    """
    prepared = []

    for index, original in enumerate(recommendations):
        item = dict(original)

        item["_originalIndex"] = index
        item["marketPerformance"] = {
            "status": "not_checked",
            "records": [],
        }

        prepared.append(item)

    # 조회량 제한
    for item in prepared[:max_lookups]:
        result = get_market_performance(
            item.get("itemSeq"),
            item.get("medicineName"),
        )

        item["marketPerformance"] = result

    # 증상 적합성 그룹 구성
    # 적합성 점수 필드가 없는 경우는
    # 기존 추천 순위의 연속 3개씩만 비교한다.
    def relevance_group(item):
        if relevance_field is not None:
            value = item.get(relevance_field)

            if value is not None:
                return ("score", str(value))

        return (
            "rank",
            item["_originalIndex"] // 3,
        )

    groups = {}

    for item in prepared:
        group_key = relevance_group(item)
        groups.setdefault(group_key, []).append(item)

    final = []

    for group in groups.values():
        # 실적을 비교할 수 있는 제품만
        # 같은 연도와 같은 실적 구분끼리 정렬.
        positions = {}

        for index, item in enumerate(group):
            record = _comparable_record(
                item["marketPerformance"]
            )

            if record is None:
                continue

            key = (
                record["year"],
                record["part"],
            )

            positions.setdefault(key, []).append(index)

        for indexes in positions.values():
            selected = [
                group[index]
                for index in indexes
            ]

            selected.sort(
                key=lambda item: -_comparable_record(
                    item["marketPerformance"]
                )["amount"]
            )

            for index, item in zip(indexes, selected):
                group[index] = item

        final.extend(group)

    for item in final:
        item.pop("_originalIndex", None)

    return final
