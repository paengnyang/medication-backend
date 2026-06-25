from firebase_config import db


def check_allergy_warning(user_id: str, medicine_name: str):
    """
    사용자 allergyList와 약 이름을 비교하여 알레르기 경고를 반환한다.
    현재는 성분 DB가 없기 때문에 약 이름 기준으로 1차 검사한다.
    """

    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return None

    user_data = user_doc.to_dict()
    allergy_list = user_data.get("allergyList", [])

    if not allergy_list:
        return None

    for allergy in allergy_list:
        if allergy in medicine_name:
            return f"알레르기 주의: {allergy} 관련 약물이 포함되어 있습니다."

    return None