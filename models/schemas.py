from pydantic import BaseModel, Field


# 복약 일정 생성 요청 데이터 형식
class ScheduleCreateRequest(BaseModel):
    userId: str
    medicineName: str
    dailyCount: int
    dosage: float
    timing: str
    startDate: str
    endDate: str
    period: int


# 복용 완료 요청 데이터 형식
class TakenLogRequest(BaseModel):
    userId: str
    scheduleId: str
    medicineName: str
    date: str
    time: str

# 사용자 생성 요청 데이터 형식
class UserCreateRequest(BaseModel):
    email: str
    password: str
    name: str
    phone: str 

    gender: str
    pregnancy: str = ""

    birthYear: str
    birthMonth: str
    birthDay: str

    guardianPhone: str = ""
    
    allergyList: list[str] = Field(default_factory=list)
    verificationToken: str = Field(..., min_length=1)
    
class UserLoginRequest(BaseModel):
    email: str
    password: str

class StockCreateRequest(BaseModel):
    userId: str
    medicineName: str
    remainingCount: int
    setSize: int


class StockConsumeRequest(BaseModel):
    userId: str
    stockId: str


class StockAddSetRequest(BaseModel):
    userId: str
    stockId: str

class MedicationTimesUpdateRequest(BaseModel):
    morning: str
    lunch: str
    dinner: str

from pydantic import BaseModel
from typing import List


class AllergyListUpdateRequest(
    BaseModel
):
    allergyList: List[str]