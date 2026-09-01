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

class UserLoginRequest(BaseModel):
    email: str
    password: str