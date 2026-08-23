"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import AppNav from "@/components/AppNav";
import { Card, Empty, Field, Skeleton, Status, moment } from "@/components/ui/Primitives";
import { api, getToken, ListResponse, ProfileListItem } from "@/lib/api";
import { questionnaireLabel, statusLabel, statusTone } from "@/lib/labels";

export default function ProfilesPage() {
  const [data, setData] = useState<ListResponse<ProfileListItem> | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);

  function load(searchValue: string, statusValue: string) {
    setLoading(true);
    api
      .profiles({ search: searchValue || undefined, status: statusValue || undefined })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load("", "");
  }, []);

  const filtered = search !== "" || status !== "";

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Анкеты</h1>
          <p className="page-subtitle">
            Что человек рассказал о себе боту: цель, опыт, ограничения. На основе
            анкеты собирается программа тренировок.
          </p>
        </div>

        {error && <div className="error">{error}</div>}

        <Card>
          <div className="filters">
            <Field
              label="Поиск"
              hint="Имя, номер анкеты или её идентификатор."
              htmlFor="profiles-search"
            >
              <input
                id="profiles-search"
                type="search"
                placeholder="Например: Иван или 1042"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load(search, status)}
              />
            </Field>
            <Field
              label="Состояние анкеты"
              hint="Черновик — человек ещё отвечает на вопросы."
              htmlFor="profiles-status"
            >
              <select
                id="profiles-status"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">Любое</option>
                <option value="confirmed">Подтверждена</option>
                <option value="in_progress">Заполняется</option>
                <option value="draft">Черновик</option>
              </select>
            </Field>
            <div className="filters-actions">
              <button
                type="button"
                className="primary"
                onClick={() => load(search, status)}
              >
                Показать
              </button>
              {filtered && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearch("");
                    setStatus("");
                    load("", "");
                  }}
                >
                  Сбросить
                </button>
              )}
            </div>
          </div>
        </Card>

        <Card
          title="Список анкет"
          description={data ? `Найдено: ${data.total}` : undefined}
        >
          {loading && <Skeleton rows={4} />}

          {!loading && data && data.items.length === 0 && (
            <Empty
              title={filtered ? "Ничего не нашлось" : "Анкет пока нет"}
              hint={
                filtered
                  ? "Попробуйте изменить условия поиска или сбросить фильтры."
                  : "Анкета появляется, когда человек проходит опрос в боте."
              }
            />
          )}

          {!loading && data && data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Кто</th>
                    <th>Номер</th>
                    <th>Возраст</th>
                    <th>Цель</th>
                    <th>Состояние</th>
                    <th>Создана</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((p) => (
                    <tr key={p.profile_id}>
                      <td>
                        <Link href={`/profiles/${p.profile_id}`}>
                          {p.name || "без имени"}
                        </Link>
                      </td>
                      <td>{p.display_number || "—"}</td>
                      <td>{p.age ?? "—"}</td>
                      <td>
                        {p.primary_goal ? questionnaireLabel(p.primary_goal) : "—"}
                      </td>
                      <td>
                        <Status tone={statusTone(p.status)}>
                          {statusLabel(p.status)}
                        </Status>
                      </td>
                      <td className="muted">{moment(p.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </main>
    </div>
  );
}
