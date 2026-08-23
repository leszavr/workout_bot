/** @type {import('next').NextConfig} */
const nextConfig = {
  // Каталог сборки можно увести в сторону через WEB_DIST_DIR.
  //
  // `next dev` и `next build` по умолчанию пишут в один и тот же `.next`, и
  // проверочная production-сборка при работающем dev-сервере перемешивает
  // манифесты с чанками: dev-сервер падает с «Cannot find module './948.js'».
  // Проверка со своим каталогом (WEB_DIST_DIR=.next-check npm run build)
  // не задевает работающий dev-сервер.
  distDir: process.env.WEB_DIST_DIR || ".next",
};

export default nextConfig;
