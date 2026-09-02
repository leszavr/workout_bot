"""worker retry/recovery: next_attempt_at, аренда job и retry доставок

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-02

Phase 1.2-D. До этой миграции у операции генерации не было места, где хранится
ответ на два вопроса: «когда её повторить» и «кто её сейчас выполняет».
Оба ответа обязаны лежать в PostgreSQL: worker переживает рестарт, а несколько
его экземпляров не должны брать один job.

Что добавляется в `generation_jobs`:

- `next_attempt_at` — момент, с которого повтор допустим. NULL означает «повтор
  не назначен»: так выглядят и успешные job, и окончательные отказы. Отдельный
  статус `RETRY_WAIT` не вводится — `FAILED` уже означает «попытка не удалась»,
  и второе значение с тем же смыслом заставило бы каждого читателя проверять
  два статуса вместо одного;
- `lease_owner` / `lease_expires_at` — аренда захваченного job. Без них
  «застрявший в RUNNING» неотличим от «выполняется прямо сейчас»: единственная
  доступная метка `started_at` растёт вместе с длительностью легальной
  генерации, которая у AI-контура доходит до 30 минут.

Что добавляется в `program_deliveries`: те же `next_attempt_at`,
`lease_owner`, `lease_expires_at`. Retry доставки уже существовал внутри одного
вызова (три попытки с backoff), но между вызовами состояние не сохранялось, и
`failed`-запись никто не подхватывал.

Индексы обслуживают ровно один запрос воркера — «что можно взять сейчас»:
частичный индекс по (status, next_attempt_at) для очереди повторов и индекс по
(status, lease_expires_at) для поиска просроченной аренды. Полные индексы по
этим колонкам были бы больше и бесполезнее: worker никогда не спрашивает
succeeded-записи.

Миграция аддитивная: все колонки nullable, существующие строки не меняются, у
исторических `failed`-job `next_attempt_at` остаётся NULL. Это осознанно —
задним числом планировать повтор для отказов, случившихся до появления воркера,
значит запустить генерацию по анкетам, о которых уже никто не ждёт ответа.
Downgrade удаляет только созданное.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Одинаковый набор колонок для generation_jobs и program_deliveries: retry и
# аренда — одно и то же свойство фоновой операции, независимо от её предмета.
_RETRY_COLUMNS = (
    ("next_attempt_at", sa.DateTime(timezone=True)),
    ("lease_owner", sa.String(64)),
    ("lease_expires_at", sa.DateTime(timezone=True)),
)


def _add_retry_columns(table: str) -> None:
    for name, type_ in _RETRY_COLUMNS:
        op.add_column(table, sa.Column(name, type_, nullable=True))


def _drop_retry_columns(table: str) -> None:
    for name, _ in reversed(_RETRY_COLUMNS):
        op.drop_column(table, name)


def upgrade() -> None:
    _add_retry_columns("generation_jobs")
    _add_retry_columns("program_deliveries")

    # Очередь повторов: только записи, которые вообще могут быть взяты.
    op.create_index(
        "ix_generation_jobs_retry_queue",
        "generation_jobs",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )
    op.create_index(
        "ix_program_deliveries_retry_queue",
        "program_deliveries",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )
    # Поиск просроченной аренды после падения процесса.
    op.create_index(
        "ix_generation_jobs_lease",
        "generation_jobs",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("lease_expires_at IS NOT NULL"),
    )
    op.create_index(
        "ix_program_deliveries_lease",
        "program_deliveries",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("lease_expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_program_deliveries_lease", table_name="program_deliveries")
    op.drop_index("ix_generation_jobs_lease", table_name="generation_jobs")
    op.drop_index(
        "ix_program_deliveries_retry_queue", table_name="program_deliveries"
    )
    op.drop_index("ix_generation_jobs_retry_queue", table_name="generation_jobs")
    _drop_retry_columns("program_deliveries")
    _drop_retry_columns("generation_jobs")
