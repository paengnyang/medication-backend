from pydantic import BaseModel


# 복약 일정 생성 요청 데이터 형식
class ScheduleCreateRequest(BaseModel):
    userId: str
    medicineName: str
    dailyCount: int
    dosage: int
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
    userId: str
    name: str
    email: str
    age: int
    allergyList: list[str] = []