"""ai_endpoints: результат последней проверки подключения

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-21

Phase 1.1 (AI configuration UX): результат connection test становится
персистентным состоянием эндпоинта, чтобы отчёт готовности AI мог честно
ответить «подключение проверено / не проверено / проверка провалилась»
без выполнения живого запроса к провайдеру.

Хранится только технический результат: время, статус (success|error) и
класс ошибки. Ни ключей, ни текста ответа провайдера здесь нет.

Аддитивная миграция: существующие эндпоинты получают NULL — состояние
«подключение никогда не проверялось».
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_endpoints "
        "ADD COLUMN IF NOT EXISTS last_test_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE ai_endpoints "
        "ADD COLUMN IF NOT EXISTS last_test_status VARCHAR(16)"
    )
    op.execute(
        "ALTER TABLE ai_endpoints "
        "ADD COLUMN IF NOT EXISTS last_test_error_type VARCHAR(100)"
    )


def downgrade() -> None:
    op.drop_column("ai_endpoints", "last_test_error_type")
    op.drop_column("ai_endpoints", "last_test_status")
    op.drop_column("ai_endpoints", "last_test_at")
