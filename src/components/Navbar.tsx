"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useSession, signOut } from "next-auth/react";

const primary = [
  { href: "/universities", label: "Universities" },
  { href: "/programs", label: "Programs" },
  { href: "/professors", label: "Professors" },
  { href: "/find", label: "Find My Match" },
  { href: "/scholarship", label: "Funding" },
];

/** Secondary features only — no standalone Applications / Shortlist / Deadlines / Notices / Get started. */
const moreLinks = [
  { href: "/compare", label: "Compare" },
  { href: "/apply", label: "Apply for Me" },
  { href: "/assistant", label: "Assistant" },
  { href: "/support", label: "Support" },
];

/** Same labels as desktop primary — keeps branding and navigation unified. */
const mobilePublic = [
  { href: "/", label: "Home" },
  { href: "/universities", label: "Universities" },
  { href: "/programs", label: "Programs" },
  { href: "/professors", label: "Professors" },
  { href: "/find", label: "Find My Match" },
  { href: "/scholarship", label: "Funding" },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/";
  const path = href.split("#")[0];
  return pathname === path || pathname.startsWith(path + "/");
}

function itemClass(active: boolean) {
  return `nav-item rounded-md px-2.5 py-2 text-sm ${
    active ? "nav-active" : "text-[var(--gray-700)] hover:bg-[var(--gray-100)]"
  }`;
}

export default function Navbar() {
  const pathname = usePathname();
  const { data: session, status } = useSession();
  const plan = (session?.user as { plan?: string } | undefined)?.plan;
  const role = (session?.user as { role?: string } | undefined)?.role;
  const pro = plan === "pro";
  const isAdmin = role === "admin";
  const [moreOpen, setMoreOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const more = moreLinks;
  const mobilePrimary = mobilePublic;
  const moreActive = more.some((item) => isActive(pathname, item.href));

  useEffect(() => {
    setMoreOpen(false);
    setAccountOpen(false);
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    function onPointer(event: MouseEvent) {
      const target = event.target as Node;
      if (moreRef.current && !moreRef.current.contains(target)) setMoreOpen(false);
      if (accountRef.current && !accountRef.current.contains(target)) setAccountOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMoreOpen(false);
        setAccountOpen(false);
        setMobileOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const name = session?.user?.name || session?.user?.email || "Account";
  const initial = name.slice(0, 1).toUpperCase();

  return (
    <header className="sticky top-0 z-50 border-b border-[var(--gray-200)] bg-[var(--paper)]">
      <div className="page-container flex h-14 items-center justify-between gap-3">
        <Link href="/" className="shrink-0 flex items-center" aria-label="ProFinder home">
          <Image src="/logo.png" alt="ProFinder Now" width={168} height={51} priority className="h-8 w-auto" />
        </Link>

        <nav className="hidden items-center gap-0.5 lg:flex" aria-label="Primary">
          {primary.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={itemClass(isActive(pathname, item.href))}
              aria-current={isActive(pathname, item.href) ? "page" : undefined}
            >
              {item.label}
            </Link>
          ))}
          {isAdmin && (
            <Link
              href="/admin"
              className={itemClass(isActive(pathname, "/admin"))}
              aria-current={isActive(pathname, "/admin") ? "page" : undefined}
            >
              Admin
            </Link>
          )}
          <div className="relative" ref={moreRef}>
            <button
              type="button"
              className={itemClass(moreActive)}
              aria-expanded={moreOpen}
              aria-haspopup="menu"
              onClick={() => setMoreOpen((open) => !open)}
            >
              More
            </button>
            {moreOpen && (
              <div
                role="menu"
                className="menu-panel absolute left-0 z-50 mt-1 w-48 rounded-md border border-[var(--gray-200)] bg-white p-1"
              >
                {more.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    {item.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        </nav>

        <div className="flex items-center gap-2">
          <Link
            href={pro ? "/billing" : "/pricing"}
            className="rounded-md px-2.5 py-2 text-sm font-medium text-[var(--navy)] hover:bg-[var(--gray-100)]"
          >
            {pro ? "Pro" : "Go Pro"}
          </Link>
          {status === "loading" ? (
            <span className="text-xs text-[var(--gray-500)]">…</span>
          ) : session ? (
            <div className="relative" ref={accountRef}>
              <button
                type="button"
                className="grid h-8 w-8 place-items-center rounded-full border border-[var(--gray-200)] text-xs font-medium text-[var(--navy)]"
                aria-expanded={accountOpen}
                aria-haspopup="menu"
                aria-label="Account menu"
                onClick={() => setAccountOpen((open) => !open)}
              >
                {initial}
              </button>
              {accountOpen && (
                <div
                  role="menu"
                  className="menu-panel absolute right-0 z-50 mt-1 w-56 rounded-md border border-[var(--gray-200)] bg-white p-1"
                >
                  <div className="px-3 py-2">
                    <p className="truncate text-sm font-medium text-[var(--navy)]">{name}</p>
                    <p className="text-xs text-[var(--gray-500)]">{pro ? "ProFinder Pro" : "Free plan"}{isAdmin ? " · Admin" : ""}</p>
                  </div>
                  <Link
                    href="/profile"
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    Profile
                  </Link>
                  <Link
                    href="/tracker"
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    My workspace
                  </Link>
                  <Link
                    href="/shortlist"
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    Shortlist
                  </Link>
                  <Link
                    href="/billing"
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    Billing & Subscription
                  </Link>
                  {isAdmin && (
                    <Link
                      href="/admin"
                      role="menuitem"
                      className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                    >
                      Admin dashboard
                    </Link>
                  )}
                  <Link
                    href="/support"
                    role="menuitem"
                    className="block rounded-md px-3 py-2 text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                  >
                    Support
                  </Link>
                  <div className="my-1 border-t border-[var(--gray-200)]" />
                  <button
                    type="button"
                    role="menuitem"
                    className="block w-full rounded-md px-3 py-2 text-left text-sm text-[var(--gray-700)] hover:bg-[var(--gray-50)]"
                    onClick={() => signOut({ callbackUrl: "/" })}
                  >
                    Sign out
                  </button>
                </div>
              )}
            </div>
          ) : (
            <>
              <Link href="/login" className="btn-ghost text-sm">
                Log in
              </Link>
              <Link href="/signup" className="btn-primary hidden sm:inline-flex">
                Sign up
              </Link>
            </>
          )}
          <button
            type="button"
            className="rounded-md px-2.5 py-2 text-sm text-[var(--navy)] lg:hidden"
            aria-expanded={mobileOpen}
            aria-label="Open menu"
            onClick={() => setMobileOpen((open) => !open)}
          >
            Menu
          </button>
        </div>
      </div>

      <nav
        className="flex gap-1 overflow-x-auto border-t border-[var(--gray-200)] px-4 py-2 lg:hidden"
        aria-label="Mobile"
      >
        {mobilePrimary.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={itemClass(isActive(pathname, item.href))}
            aria-current={isActive(pathname, item.href) ? "page" : undefined}
          >
            {item.label}
          </Link>
        ))}
        {isAdmin && (
          <Link
            href="/admin"
            className={itemClass(isActive(pathname, "/admin"))}
            aria-current={isActive(pathname, "/admin") ? "page" : undefined}
          >
            Admin
          </Link>
        )}
      </nav>
      {mobileOpen && (
        <div className="menu-panel border-t border-[var(--gray-200)] bg-white px-4 py-3 lg:hidden">
          <div className="grid gap-1">
            {more.map((item) => (
              <Link key={item.href} href={item.href} className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                {item.label}
              </Link>
            ))}
            <Link href="/pricing" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
              Pricing
            </Link>
            {session ? (
              <>
                <Link href="/profile" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                  Profile
                </Link>
                <Link href="/tracker" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                  My workspace
                </Link>
                <Link href="/shortlist" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                  Shortlist
                </Link>
                <Link href="/billing" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                  Billing & Subscription
                </Link>
                {isAdmin && (
                  <Link href="/admin" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                    Admin dashboard
                  </Link>
                )}
                <button
                  type="button"
                  className="rounded-md px-2 py-2 text-left text-sm text-[var(--gray-700)]"
                  onClick={() => signOut({ callbackUrl: "/" })}
                >
                  Sign out
                </button>
              </>
            ) : (
              <Link href="/login" className="rounded-md px-2 py-2 text-sm text-[var(--gray-700)]">
                Log in
              </Link>
            )}
          </div>
        </div>
      )}
    </header>
  );
}
