Внутренний веб-интерфейс Workout Bot (Next.js App Router).

## Локальная разработка

```bash
npm install
npm run dev      # http://localhost:3000
```

Нужен работающий backend; его адрес задаётся в `.env.local`
(`NEXT_PUBLIC_API_BASE`, по умолчанию `http://localhost:8000`).

## Не запускайте `npm run build` при работающем `npm run dev`

`next build` и `next dev` пишут в один и тот же каталог `.next`. Если запустить
production-сборку, пока dev-сервер работает, его манифесты и чанки
перемешиваются с production-артефактами, и dev-сервер начинает падать:

```
Error: Cannot find module './948.js'
```

Файл при этом на диске есть — просто собран другим прогоном, и ссылки на чанки
не совпадают. Ошибка не связана с кодом. Лечится так:

```bash
# остановить dev-сервер, затем
rm -rf .next
npm run dev
```

Перед проверочной production-сборкой останавливайте dev-сервер. В CI конфликта
нет: там сборка идёт в чистом окружении, dev-сервера не существует.

## Проверки

```bash
npm run lint
npx tsc --noEmit
npm run build
```

---

This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
