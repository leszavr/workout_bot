"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/profiles", label: "Профили" },
  { href: "/programs", label: "Программы" },
  { href: "/exercises", label: "Упражнения" },
];

export default function AppNav() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="brand">🏋️ Workout Bot</div>
      <nav>
        {NAV.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} className={active ? "active" : ""}>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
