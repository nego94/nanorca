"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/",         label: "Dashboard" },
  { href: "/trades",   label: "Trades" },
  { href: "/report",   label: "Report" },
  { href: "/settings", label: "Settings" },
];

export default function NavBar() {
  const path = usePathname();
  return (
    <nav className="border-b border-brand-border bg-brand-card">
      <div className="max-w-7xl mx-auto px-4 flex items-center gap-6 h-14">
        <span className="font-bold text-white tracking-tight text-lg">
          NANORCA
        </span>
        <div className="flex gap-1 ml-4">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                path === l.href
                  ? "bg-brand-blue text-white"
                  : "text-brand-muted hover:text-white hover:bg-brand-border"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </div>
        <div className="ml-auto text-xs text-brand-muted">
          Paper Trading Mode
        </div>
      </div>
    </nav>
  );
}
