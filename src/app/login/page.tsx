"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    // Preserve internal destinations (such as /admin) without allowing external redirects.
    const requestedCallback = new URLSearchParams(window.location.search).get("callbackUrl");
    const callbackUrl =
      requestedCallback?.startsWith("/") && !requestedCallback.startsWith("//")
        ? requestedCallback
        : "/tracker";

    const res = await signIn("credentials", {
      email,
      password,
      callbackUrl,
      redirect: false,
    });

    setLoading(false);

    if (res?.error) {
      setError("Invalid email or password.");
      return;
    }

    router.push(callbackUrl);
    router.refresh();
  }

  return (
    <div className="page-container flex min-h-[70vh] items-center justify-center py-12">
      <div className="w-full max-w-md">
        <div className="text-center">
          <h1 className="section-title">Log in</h1>
          <p className="mt-1 text-sm text-gray-500">
            Access your matches, tracker, and Pro features
          </p>
        </div>

        <form onSubmit={handleSubmit} className="card mt-8 space-y-4 p-6">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Email
            </label>
            <input
              type="email"
              className="input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Password
            </label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                className="input pr-16"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-xs font-medium text-[var(--teal)]"
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <button type="submit" className="btn-primary w-full" disabled={loading}>
            {loading ? "Signing in..." : "Log in"}
          </button>
        </form>
        <p className="mt-3 text-center text-sm"><a href="/forgot-password" className="text-[var(--teal)]">Forgot password?</a></p>

        <p className="mt-4 text-center text-sm text-gray-600">
          Don&apos;t have an account?{" "}
          <Link href="/signup" className="font-medium text-[var(--teal)] hover:underline">
            Sign up free
          </Link>
        </p>
      </div>
    </div>
  );
}
