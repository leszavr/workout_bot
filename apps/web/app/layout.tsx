import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Workout Bot — внутренний интерфейс",
  description: "Внутренний инструмент администратора и разработчика",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
