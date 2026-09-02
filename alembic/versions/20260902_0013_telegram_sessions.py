"""telegram_sessions: серверная сессия анкеты

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-02

Вынос Telegram Gateway за сетевую границу. До этой миграции состояние анкеты
жило в Redis Gateway: там лежал целиком сериализованный профиль — имя, возраст,
рост, вес, ограничения движений, рекомендации врача. Gateway размещается в
EU-регионе, поэтому такое хранение означало персональные данные, включая данные
о здоровье, за пределами RU.

Таблица переносит это состояние в RU. Хранится:

- `position` — идентификатор вопроса, на котором стоит диалог, либо служебный
  экран (`review`, `confirm`). Отдельного enum нет: значения приходят из
  декларации анкеты, и дублировать её в схеме БД значит гарантированно с ней
  разойтись;
- `draft` — черновик профиля (JSONB). Он появляется с первого ответа, а не при
  финализации: прерванная анкета иначе теряется целиком, и человек, ответивший
  на тридцать вопросов, начинает заново;
- `editing_question` — вопрос, который правится из сводки. Без него после
  правки диалог уходил бы в следующий по порядку вопрос вместо возврата в сводку;
- `last_update_id` / `last_view` — идемпотентность. Telegram переотправляет
  обновление, если его не подтвердили, а Gateway повторяет запрос при таймауте.
  Без ключа один ответ пользователя продвинул бы анкету на два шага. Хранится
  не только номер, но и отданный ответ: повтор обязан вернуть тот же вид, иначе
  Gateway нечего показать.

Почему отдельная таблица, а не колонки в `profiles`. Сессия существует до
профиля: у диалога, начатого и брошенного на первом вопросе, профиля нет и он
не должен появляться в списке анкет администратора. Строка `profiles` со
статусом «черновик» попала бы в `GET /profiles` и в аналитику, а фильтровать её
пришлось бы во всех запросах сразу.

Ключ — `telegram_user_id`: у пользователя Telegram один активный диалог с ботом.
Уникальность выражена ограничением, а не соглашением: без него параллельные
обновления от одного пользователя создали бы две сессии, и анкета раздвоилась бы.

`profile_id` заполняется при финализации и связывает сессию с созданным
профилем. FK нет намеренно: сессия живёт своей жизнью, а удаление профиля
администратором не должно ни падать, ни каскадно чистить сессию — в ней уже
может идти новая анкета.

Миграция аддитивная: новая таблица, существующие данные не меняются. Незавершённые
анкеты в Redis эта миграция не переносит — они несовместимы по смыслу (там лежит
профиль, здесь позиция и черновик), и Gateway при первом же событии переспросит
текущий вопрос, создав серверную сессию. Пользователь увидит сообщение, а не
молчание.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("position", sa.String(64), nullable=True),
        sa.Column("editing_question", sa.String(64), nullable=True),
        sa.Column("draft", JSONB, nullable=True),
        sa.Column("profile_id", sa.String(64), nullable=True),
        sa.Column("last_update_id", sa.BigInteger, nullable=True),
        sa.Column("last_view", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("telegram_user_id", name="uq_telegram_session_user"),
    )
    # Поиск сессии по созданному профилю: нужен операционным запросам
    # («какая сессия привела к этой анкете»), частичный — сессий без профиля
    # большинство.
    op.create_index(
        "ix_telegram_sessions_profile",
        "telegram_sessions",
        ["profile_id"],
        postgresql_where=sa.text("profile_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_sessions_profile", table_name="telegram_sessions")
    op.drop_table("telegram_sessions")
