"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Field } from "@/components/ui/Primitives";
import { login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(loginName, password);
      // Временный пароль: остальные разделы всё равно закрыты сервером,
      // поэтому ведём сразу на смену пароля.
      router.push(result.must_change_password ? "/change-password" : "/");
    } catch {
      // Не уточняем, что именно не подошло: подсказка помогала бы подбору.
      setError("Не удалось войти. Проверьте логин и пароль.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Вход</h1>
        <p className="login-sub">Внутренний интерфейс Workout Bot</p>

        {error && <div className="error">{error}</div>}

        <Field label="Логин" htmlFor="login-name">
          <input
            id="login-name"
            type="text"
            value={loginName}
            onChange={(e) => setLoginName(e.target.value)}
            autoComplete="username"
          />
        </Field>

        <Field label="Пароль" htmlFor="login-password">
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </Field>

        <button
          type="submit"
          className="primary"
          disabled={busy || !loginName || !password}
        >
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
