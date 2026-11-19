import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { REPO } from "../links.js";

const links = [
  { to: "/", label: "Home", end: true },
  { to: "/about", label: "About" },
  { to: "/contact", label: "Contact" },
];

function itemClass({ isActive }) {
  return isActive
    ? "text-white"
    : "text-mist hover:text-white";
}

export default function Layout() {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[radial-gradient(1200px_500px_at_80%_-10%,rgba(124,92,255,0.28),transparent),linear-gradient(180deg,#0c1222_0%,#101a30_40%,#0c1222_100%)]">
      <header className="sticky top-0 z-20 border-b border-white/10 bg-ink/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-3">
          <NavLink to="/" className="flex items-center gap-3" onClick={() => setOpen(false)}>
            <img src="/agen-mark.png" alt="" className="h-9 w-9 rounded-lg" />
            <span className="text-lg font-semibold tracking-tight">Agen</span>
          </NavLink>
          <nav className="hidden items-center gap-7 text-sm font-medium md:flex">
            {links.map((link) => (
              <NavLink key={link.to} to={link.to} end={link.end} className={itemClass}>
                {link.label}
              </NavLink>
            ))}
            <NavLink
              to="/download"
              className="rounded-full bg-violet px-4 py-2 text-white shadow-[0_0_24px_rgba(124,92,255,0.35)]"
            >
              Download
            </NavLink>
          </nav>
          <button
            type="button"
            className="rounded-md border border-white/15 px-3 py-2 text-sm md:hidden"
            aria-expanded={open}
            aria-label="Open menu"
            onClick={() => setOpen((value) => !value)}
          >
            Menu
          </button>
        </div>
        {open && (
          <nav className="flex flex-col gap-3 border-t border-white/10 px-5 py-4 text-sm md:hidden">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={itemClass}
                onClick={() => setOpen(false)}
              >
                {link.label}
              </NavLink>
            ))}
            <NavLink
              to="/download"
              className="w-fit rounded-full bg-violet px-4 py-2 text-white"
              onClick={() => setOpen(false)}
            >
              Download
            </NavLink>
          </nav>
        )}
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="border-t border-white/10">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-5 py-8 text-sm text-mist sm:flex-row sm:items-center sm:justify-between">
          <p>Agen is built by Josephat Oyondi. MIT license.</p>
          <a className="text-lilac hover:text-white" href={REPO}>
            github.com/ItsOyondi/agen
          </a>
        </div>
      </footer>
    </div>
  );
}
