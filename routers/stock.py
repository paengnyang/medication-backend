from fastapi import APIRouter, HTTPException
from datetime import datetime
import uuid

from firebase_config import db
from models.schemas import (
    StockCreateRequest,
    StockConsumeRequest,
    StockAddSetRequest,
)

router = APIRouter(
    prefix="/stocks",
    tags=["Stocks"]
)


# 상비약 추가
@router.post("/add")
def add_stock(data: StockCreateRequest):

    # 같은 사용자 + 같은 약이 이미 있는지 확인
    docs = list(
        db.collection("stockMedicines")
        .where("userId", "==", data.userId)
        .where("medicineName", "==", data.medicineName)
        .stream()
    )

    # 이미 있으면 재고 추가
    if docs:
        doc = docs[0]
        current = doc.to_dict()

        new_remaining = (
            current.get("remainingCount", 0)
            + data.remainingCount
        )

        doc.reference.update({
            "remainingCount": new_remaining,
            "setSize": data.setSize,
            "updatedAt": datetime.now().isoformat(),
        })

        return {
            "success": True,
            "message": "상비약 재고 추가 완료",
            "stockId": doc.id,
            "remainingCount": new_remaining,
        }

    # 처음 등록하는 약
    stock_id = str(uuid.uuid4())

    stock_data = {
        "stockId": stock_id,
        "userId": data.userId,
        "medicineName": data.medicineName,
        "remainingCount": data.remainingCount,
        "setSize": data.setSize,
        "createdAt": datetime.now().isoformat(),
    }

    db.collection("stockMedicines").document(stock_id).set(
        stock_data
    )

    return {
        "success": True,
        "message": "상비약 등록 완료",
        "stock": stock_data,
    }


# 로그인 사용자의 상비약 조회
@router.get("/user/{user_id}")
def get_user_stocks(user_id: str):

    docs = (
        db.collection("stockMedicines")
        .where("userId", "==", user_id)
        .stream()
    )

    stocks = []

    for doc in docs:
        stocks.append(doc.to_dict())

    return {
        "success": True,
        "count": len(stocks),
        "stocks": stocks,
    }


# 알약 1개 사용
@router.post("/consume")
def consume_stock(data: StockConsumeRequest):

    ref = db.collection("stockMedicines").document(
        data.stockId
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail="상비약을 찾을 수 없습니다."
        )

    stock = doc.to_dict()

    # 다른 사용자의 약 수정 방지
    if stock.get("userId") != data.userId:
        raise HTTPException(
            status_code=403,
            detail="접근 권한이 없습니다."
        )

    remaining = stock.get("remainingCount", 0)

    if remaining <= 0:
        return {
            "success": False,
            "message": "남은 약이 없습니다.",
            "remainingCount": 0,
        }

    remaining -= 1

    ref.update({
        "remainingCount": remaining,
    })

    return {
        "success": True,
        "remainingCount": remaining,
    }


# 한 판 추가
@router.post("/add-set")
def add_stock_set(data: StockAddSetRequest):

    ref = db.collection("stockMedicines").document(
        data.stockId
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail="상비약을 찾을 수 없습니다."
        )

    stock = doc.to_dict()

    if stock.get("userId") != data.userId:
        raise HTTPException(
            status_code=403,
            detail="접근 권한이 없습니다."
        )

    remaining = stock.get("remainingCount", 0)
    set_size = stock.get("setSize", 0)

    new_remaining = remaining + set_size

    ref.update({
        "remainingCount": new_remaining,
    })

    return {
        "success": True,
        "remainingCount": new_remaining,
    }