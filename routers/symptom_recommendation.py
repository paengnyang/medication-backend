from typing import Optional

from fastapi import (
    APIRouter,
)

from pydantic import (
    BaseModel,
    Field,
)

from services.symptom_recommendation_service import (
    recommend_by_symptom,
)


router = APIRouter(
    prefix="/symptom",
    tags=["Symptom Recommendation"],
)


class SymptomRecommendRequest(
    BaseModel
):
    symptom: str

    userId: Optional[str] = None

    limit: int = Field(
        default=5,
        ge=1,
        le=10,
    )


@router.post("/recommend")
def recommend_medicine(
    request:
        SymptomRecommendRequest,
):
    return recommend_by_symptom(
        symptom=request.symptom,
        user_id=request.userId,
        limit=request.limit,
    )