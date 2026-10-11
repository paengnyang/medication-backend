from fastapi import FastAPI
from firebase_config import db
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware

from routers.schedule import router as schedule_router
from routers.log import router as log_router
from routers.user import router as user_router
from routers.ocr import router as ocr_router
from routers.stock import router as stock_router
from routers.drugs import router as drugs_router
from routers.safety import router as safety_router
from routers.password_reset import router as password_reset_router
from routers.guardians import router as guardians_router
from routers.interactions import router as interactions_router
from routers import dose_info
from routers import package_info
from routers import allergy
from routers import symptom_recommendation




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
app.include_router(drugs_router)
app.include_router(safety_router)
app.include_router(password_reset_router)
app.include_router(guardians_router)
app.include_router(interactions_router)
app.include_router(dose_info.router)
app.include_router(package_info.router)
app.include_router(allergy.router)
app.include_router(symptom_recommendation.router)

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