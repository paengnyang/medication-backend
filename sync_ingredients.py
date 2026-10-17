from dotenv import load_dotenv

load_dotenv()

from services.ingredient_service import sync_ingredients


if __name__ == "__main__":
    saved = sync_ingredients(max_pages=10)

    print(f"동기화 완료: 신규 성분 {saved}개")