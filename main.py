from fastapi import FastAPI
from firebase_config import db
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware

from routers.schedule import router as schedule_router
from routers.log import router as log_router
from routers.user import router as user_router
from routers.ocr import router as ocr_router
from routers.stock import router as stock_router



app = FastAPI(
    title="Medication Management API",
    description="AI 기반 개인 맞춤 복약 관리 시스템 백엔드 서버",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 중에는 전체 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# schedule.py와 log.py에 있는 API들을 main.py에 등록
app.include_router(schedule_router)
app.include_router(log_router)
app.include_router(user_router)
app.include_router(ocr_router)
app.include_router(stock_router)

@app.get("/")
def root():
    return {
        "message": "Medication API Server is running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "message": "FastAPI server is running"
    }


@app.get("/firebase/test")
def firebase_test():
    doc_ref = db.collection("test").document("connection")

    doc_ref.set({
        "message": "Firebase connected successfully",
        "createdAt": datetime.now().isoformat()
    })

    return {
        "success": True,
        "message": "Firebase connection test completed"
    }