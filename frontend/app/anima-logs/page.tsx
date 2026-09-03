"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useI18n } from "@/lib/i18n";

/**
 * The old address for what is now Session Logs (renamed in v0.6).
 *
 * This used to be a 308 in next.config.mjs. Static export has no server to issue one, and
 * the link was promised as permanent — so the redirect moved into the page itself. Doing it
 * this way in both build modes means there is no behaviour that only breaks in the packaged
 * build, which is the kind of difference nobody finds until after a release.
 *
 * Session Logs 这个页面的旧地址（v0.6 改的名）。
 *
 * 它原本是 next.config.mjs 里的一个 308。静态导出背后没有服务器发得出 308，而这条链接是**承诺过
 * 永久有效**的——所以跳转搬进了页面本身。两种构建模式都走这一条，就不会出现"只在打包版里坏掉"的
 * 行为差异；那种差异，往往要等发布之后才有人发现。
 */
export default function LegacyLogsRedirect() {
  const { t } = useI18n();
  const router = useRouter();
  useEffect(() => {
    router.replace("/session-logs");
  }, [router]);

  return (
    <main style={{ padding: "2rem", opacity: 0.7 }}>
      {t("Redirecting to")} <a href="/session-logs">Session Logs</a>…
    </main>
  );
}
