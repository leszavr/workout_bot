"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

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
      // поэтому ведём сразу на смену пароля, а не на панель.
      router.push(result.must_change_password ? "/change-password" : "/");
    } catch {
      setError("Неверный логин или пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Вход во внутренний интерфейс</h1>
        {error && <div className="error">{error}</div>}
        <div className="field">
          <label htmlFor="login-name">Логин</label>
          <input
            id="login-name"
            type="text"
            value={loginName}
            onChange={(e) => setLoginName(e.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label htmlFor="login-password">Пароль</label>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Вход..." : "Войти"}
        </button>
      </form>
    </div>
  );
}
