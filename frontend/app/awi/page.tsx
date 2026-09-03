"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useI18n } from "@/lib/i18n";

/**
 * The old address of the dashboard (it was called AWI before the platform became NERV).
 * Static export has no server to issue a 308, so the redirect lives in the page itself —
 * the same behaviour in both build modes.
 */
export default function AwiRedirect() {
  const { t } = useI18n();
  const router = useRouter();
  useEffect(() => {
    router.replace("/nerv");
  }, [router]);
  return (
    <main style={{ padding: "2rem", opacity: 0.7 }}>
      {t("Redirecting to")} <a href="/nerv">NERV</a>…
    </main>
  );
}
