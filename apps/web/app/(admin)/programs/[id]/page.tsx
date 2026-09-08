"use client";

// Карточка программы.
//
// Версии переключаются вкладками, а не отдельными страницами: каждая новая
// сборка не заменяет предыдущую, а добавляется рядом, и сравнивать их нужно на
// одном экране. Версия живёт в адресе, поэтому ссылку на конкретную версию
// можно передать.

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import ExerciseLink from "@/components/ExerciseLink";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  Empty,
  ErrorState,
  KeyValue,
  Skeleton,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import { ProgramExercise, ProgramResponse, api } from "@/lib/api";
import { generationSourceLabel, statusLabel, statusTone } from "@/lib/labels";

const EXERCISE_COLUMNS: ReadonlyArray<DataColumn<ProgramExercise>> = [
  { key: "order", header: "№", numeric: true, render: (ex) => ex.order },
  {
    key: "exercise",
    header: "Упражнение",
    render: (ex) => (
      <ExerciseLink
        externalId={ex.exercise_external_id}
        source={ex.exercise_source}
      >
        {ex.exercise_external_id}
      </ExerciseLink>
    ),
  },
  { key: "sets", header: "Подходов", numeric: true, render: (ex) => ex.sets },
  {
    key: "reps",
    header: "Повторений",
    numeric: true,
    render: (ex) => `${ex.repetitions_min}–${ex.repetitions_max}`,
  },
  {
    key: "rest",
    header: "Отдых",
    numeric: true,
    render: (ex) => `${ex.rest_seconds} с`,
  },
  {
    key: "notes",
    header: "Пояснение",
    render: (ex) => <span className="muted">{ex.notes ?? "—"}</span>,
  },
];

export default function ProgramDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const versionParam = searchParams.get("version");
  const version = versionParam ? Number(versionParam) : undefined;
  const [data, setData] = useState<ProgramResponse | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .program(params.id, version)
      .then((response) => {
        setData(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [params.id, version]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <>
        <ErrorState message={error} onRetry={load} />
        <Link className="btn" href="/programs">
          К списку программ
        </Link>
      </>
    );
  }

  if (!data) {
    return (
      <Card>
        <Skeleton rows={5} />
      </Card>
    );
  }

  const program = data.program;
  const byAI = program.generation.source === "ai";

  return (
    <>
      <PageHeader
        title={program.title}
        description={program.description || undefined}
        actions={
          <Link className="btn small" href={`/profiles/${program.profile_id}`}>
            Открыть анкету
          </Link>
        }
        tabs={
          data.versions.length > 1 ? (
            <nav className="tabs" aria-label="Версии программы">
              {data.versions.map((item) => (
                <button
                  key={item.version}
                  type="button"
                  className={item.version === program.version ? "active" : ""}
                  onClick={() =>
                    router.push(`/programs/${params.id}?version=${item.version}`)
                  }
                >
                  №{item.version} · {statusLabel(item.status)}
                </button>
              ))}
            </nav>
          ) : undefined
        }
      />

      <div className="inline-list" style={{ marginBottom: "var(--s-4)" }}>
        <Status tone={statusTone(program.status)}>
          {statusLabel(program.status)}
        </Status>
        <Tag tone={byAI ? "info" : "neutral"}>
          собрана: {generationSourceLabel(program.generation.source)}
        </Tag>
        <Tag>версия №{program.version}</Tag>
      </div>

      <Card title="О программе">
        <KeyValue
          rows={[
            {
              key: "Тренировок в неделю",
              value: program.training_days_per_week,
            },
            { key: "Длительность", value: `${program.duration_weeks} недель` },
            { key: "Создана", value: moment(program.created_at) },
            {
              key: "Сервис ИИ",
              value: program.generation.provider || "не сохранён",
              hidden: !byAI,
            },
            {
              key: "Модель",
              value: program.generation.model || "не сохранена",
              hidden: !byAI,
            },
            {
              key: "Версия инструкции",
              value: program.generation.prompt_version
                ? `№${program.generation.prompt_version}`
                : "не сохранена",
              hint: "Сохраняется вместе с программой, чтобы результат можно было повторить.",
              hidden: !byAI,
            },
          ]}
        />
      </Card>

      {program.training_days.map((day) => (
        <Card
          key={day.day_number}
          title={`День ${day.day_number}. ${day.title}`}
          description={day.focus ? `Основная работа: ${day.focus}` : undefined}
        >
          <DataTable
            columns={EXERCISE_COLUMNS}
            rows={day.exercises}
            rowKey={(ex) => String(ex.order)}
            emptyTitle="В этом дне упражнений нет"
            emptyHint="Программа сохранена с пустым днём: это ошибка сборки, а не отдых."
          />
        </Card>
      ))}

      {program.progression.description && (
        <Card
          title="Как усложнять"
          description="Ориентир для роста нагрузки от недели к неделе."
        >
          <p style={{ marginTop: 0 }}>{program.progression.description}</p>
          {program.progression.weekly_increase_percent !== null && (
            <p className="field-hint" style={{ marginBottom: 0 }}>
              Прибавка — до {program.progression.weekly_increase_percent}% в
              неделю.
            </p>
          )}
        </Card>
      )}

      <Card
        title="Что учтено для безопасности"
        description="Технические правила отбора движений, а не медицинское заключение."
      >
        {program.safety_notes.length === 0 ? (
          <Empty
            title="Ограничений не потребовалось"
            hint="В анкете нет ответов, из-за которых пришлось бы исключать упражнения."
          />
        ) : (
          <ul className="stack" style={{ margin: 0, paddingLeft: "1.2em" }}>
            {program.safety_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
