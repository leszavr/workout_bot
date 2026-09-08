"use client";

// Программы тренировок.
//
// Программа производна от анкеты: её всегда можно собрать заново, поэтому
// удаление здесь не блокируется ничем. Обратный порядок (удалить анкету, оставив
// программы) запрещён — см. раздел анкет.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import {
  Card,
  Notice,
  Status,
  Tag,
  moment,
} from "@/components/ui/Primitives";
import { ListResponse, ProgramListItem, api } from "@/lib/api";
import { generationSourceLabel, statusLabel, statusTone } from "@/lib/labels";
import { useCurrentUser } from "@/lib/session";

const PAGE_SIZE = 50;

export default function ProgramsPage() {
  const { canWrite } = useCurrentUser();
  const [data, setData] = useState<ListResponse<ProgramListItem> | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<ProgramListItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback((page: number) => {
    setLoading(true);
    api
      .programs({ limit: PAGE_SIZE, offset: page })
      .then((response) => {
        setData(response);
        setError("");
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load(offset);
  }, [offset, load]);

  const remove = async () => {
    if (!pending) return;
    setDeleting(true);
    try {
      await api.deleteProgram(pending.program_id);
      setNotice(`Программа «${pending.title}» удалена`);
      window.setTimeout(() => setNotice(""), 6000);
      setPending(null);
      load(offset);
    } catch (e) {
      setError((e as Error).message);
      setPending(null);
    } finally {
      setDeleting(false);
    }
  };

  const columns: ReadonlyArray<DataColumn<ProgramListItem>> = [
    {
      key: "title",
      header: "Название",
      render: (item) => (
        <>
          <Link href={`/programs/${item.program_id}`}>{item.title}</Link>
          <div className="muted" style={{ fontSize: 12 }}>
            версия №{item.version}
          </div>
        </>
      ),
    },
    {
      key: "status",
      header: "Состояние",
      render: (item) => (
        <Status tone={statusTone(item.status)}>{statusLabel(item.status)}</Status>
      ),
    },
    {
      key: "source",
      header: "Собрана",
      hint: "Кто составил план: ИИ или алгоритм подбора упражнений.",
      render: (item) => (
        <Tag tone={item.generation_source === "ai" ? "info" : "neutral"}>
          {generationSourceLabel(item.generation_source)}
        </Tag>
      ),
    },
    {
      key: "delivered",
      header: "Отправлена",
      render: (item) =>
        item.delivered ? (
          <Tag tone="ok">отправлена</Tag>
        ) : (
          <span className="muted">нет</span>
        ),
    },
    {
      key: "days",
      header: "Тренировок в неделю",
      numeric: true,
      render: (item) => item.training_days_per_week,
    },
    {
      key: "weeks",
      header: "Длительность",
      numeric: true,
      render: (item) => `${item.duration_weeks} нед.`,
    },
    {
      key: "created",
      header: "Создана",
      render: (item) => <span className="muted">{moment(item.created_at)}</span>,
    },
    {
      key: "profile",
      header: "Анкета",
      render: (item) => (
        <Link href={`/profiles/${item.profile_id}`}>открыть</Link>
      ),
    },
    {
      key: "actions",
      header: "Действия",
      hidden: !canWrite,
      render: (item) => (
        <button
          type="button"
          className="small danger"
          onClick={() => setPending(item)}
        >
          Удалить
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Программы тренировок"
        description="Готовые планы, собранные по анкетам. Столбец «Собрана» показывает, кто составил план: ИИ или алгоритм подбора упражнений."
      />

      {notice && <Notice tone="ok">{notice}</Notice>}

      <Card
        title="Список программ"
        description={data ? `Всего: ${data.total}` : undefined}
      >
        <DataTable
          columns={columns}
          rows={data?.items ?? []}
          rowKey={(item) => `${item.program_id}-v${item.version}`}
          loading={loading}
          error={error}
          emptyTitle="Программ пока нет"
          emptyHint="Откройте анкету и нажмите «Собрать программу» — план появится здесь."
          emptyAction={
            <Link className="btn" href="/profiles">
              Перейти к анкетам
            </Link>
          }
          pagination={
            data
              ? {
                  total: data.total,
                  // Сервер в этом ответе не повторяет границы страницы, поэтому
                  // они берутся из запроса, который отправил сам клиент.
                  limit: PAGE_SIZE,
                  offset,
                  onChange: setOffset,
                }
              : undefined
          }
        />
      </Card>

      {pending && (
        <ConfirmDialog
          title={`Удалить программу «${pending.title}»?`}
          description="Будут удалены все её версии и записи об отправке. Анкета останется, программу можно собрать заново."
          confirmLabel="Удалить программу"
          danger
          busy={deleting}
          onConfirm={remove}
          onCancel={() => setPending(null)}
        />
      )}
    </>
  );
}
