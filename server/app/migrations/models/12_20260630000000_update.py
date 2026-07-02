from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "reservation_conditions" (
            "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            "is_enabled" INT NOT NULL DEFAULT 1,
            "program_search_condition" JSON NOT NULL,
            "record_settings" JSON NOT NULL,
            "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "reservation_conditions";
    """
