from fastapi import APIRouter, Query

from services.ingredient_service import search_ingredients


router = APIRouter(
    prefix="/ingredients",
    tags=["Ingredients"],
)


@router.get("/search")
def search_ingredient_names(
    q: str = Query(min_length=2, max_length=100),
):
    matches = search_ingredients(q, limit=3)

    return {
        "success": True,
        "query": q,
        "count": len(matches),
        "ingredients": matches,
    }