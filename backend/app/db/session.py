from sqlalchemy import create_engine, text

from backend.app.core.config import settings

engine= create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

def test_database_connection() -> bool:
    with engine.connect() as connection:
        result= connection.execute(text("SELECT 1"))  
        return result.scalar()==1