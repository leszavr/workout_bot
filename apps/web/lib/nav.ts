// Модель навигации внутреннего интерфейса.
//
// Разделы сгруппированы по вопросу, на который отвечают, а не по тому, из
// какого модуля backend приходят данные. Главная граница — между данными
// продукта (анкеты, программы, каталог) и состоянием инфраструктуры: смешивая
// их в одном списке, администратор не отличает «у пользователя нет программы»
// от «шлюз не отвечает».
//
// Список плоский внутри группы: подразделы живут вкладками внутри страницы, а
// не пунктами меню. Иначе боковое меню разрослось бы до двух десятков ссылок,
// и найти в нём нужный раздел стало бы дольше, чем открыть его по памяти.

export interface NavItem {
  href: string;
  label: string;
  /** Короткая подпись для свёрнутого меню и подсказки. */
  icon: string;
  /** Пункт видит только администратор: сервер откажет наблюдателю. */
  adminOnly?: boolean;
  /**
   * Дополнительные адреса раздела. Нужны там, где вкладки живут по адресам,
   * не вложенным в адрес самого раздела.
   */
  extraPrefixes?: readonly string[];
}

export interface NavGroup {
  title: string;
  items: readonly NavItem[];
}

export const NAV_GROUPS: readonly NavGroup[] = [
  {
    title: "Обзор",
    items: [{ href: "/", label: "Сводка", icon: "▦" }],
  },
  {
    title: "Данные продукта",
    items: [
      { href: "/profiles", label: "Анкеты", icon: "☰" },
      { href: "/programs", label: "Программы", icon: "▤" },
    ],
  },
  {
    title: "База знаний",
    items: [
      { href: "/exercises", label: "Упражнения", icon: "⛋" },
      // «Упражнения» — что можно выполнять, «Оборудование» — чем и на чём.
      // Разделы соседние, но отвечают на разные вопросы, и словарь
      // оборудования не является частью каталога.
      { href: "/knowledge", label: "Оборудование", icon: "◈" },
      // «Внешние источники» отвечают на третий вопрос: откуда мы это узнали и
      // что решили по каждой внешней записи.
      { href: "/ingestion", label: "Внешние источники", icon: "◎" },
    ],
  },
  {
    title: "Система",
    items: [
      { href: "/ai", label: "Искусственный интеллект", icon: "◆" },
      // Состояние компонентов — чтение, поэтому доступно и наблюдателю:
      // удаление записей реестра сервер разрешает только администратору.
      { href: "/infrastructure", label: "Инфраструктура", icon: "▩" },
      { href: "/users", label: "Пользователи", icon: "◍", adminOnly: true },
    ],
  },
];

/** Пункты, доступные роли. Наблюдателю не показываем то, что сервер запретит. */
export function navGroupsFor(canWrite: boolean): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    title: group.title,
    items: group.items.filter((item) => canWrite || !item.adminOnly),
  })).filter((group) => group.items.length > 0);
}

/**
 * Активен ли пункт для текущего адреса.
 *
 * Корневой раздел сравнивается точно: иначе «Сводка» подсвечивалась бы на всех
 * страницах сразу. Остальные — по префиксу пути, чтобы карточка сущности
 * оставалась в своём разделе.
 */
export function isNavItemActive(item: NavItem, pathname: string): boolean {
  const prefixes = [item.href, ...(item.extraPrefixes ?? [])];
  return prefixes.some((prefix) =>
    prefix === "/"
      ? pathname === "/"
      : pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/** Раздел, которому принадлежит адрес: нужен для заголовка в шапке. */
export function activeNavItem(pathname: string): NavItem | null {
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      if (item.href !== "/" && isNavItemActive(item, pathname)) return item;
    }
  }
  return pathname === "/" ? NAV_GROUPS[0].items[0] : null;
}
