"use client";
import { useEffect, useState } from "react";

import { getRuntimeConfig, type RuntimeConfig } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// 运行参数条：决定一个回合能跑多深、多久、记得多少的几个闸，钉在感知区顶上。
// ⛔ 值 / env 名 / 说明全部从后端现读（GET /api/config → config.Settings），
//    前端一个数字都不写死——写两份必然出现"网页显示 60、.env 里是 8"的对不上账。
// 只读：改参数走 .env + 重启，config.py 保持单一来源。
// 同一条里还挂着 NERV_TRUST_ALL 的告警：它开着时所有节点免审——那必须一直显眼。
export default function RuntimeParamsBar() {
  const { t } = useI18n();
  const [cfg, setCfg] = useState<RuntimeConfig | null>(null);
  useEffect(() => {
    getRuntimeConfig().then(setCfg).catch(() => {});
  }, []);
  if (!cfg) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-neutral-800 bg-neutral-900/60 px-3 py-1.5 text-[11px]">
      <span className="uppercase tracking-wide text-neutral-500">{t("Runtime limits")}</span>
      {cfg.params.map((p) => (
        <span key={p.key} className="flex items-baseline gap-1" title={`${p.env}\n\n${p.description}`}>
          {/* ⭐ 后端送英文正典，界面在这里翻。标签来自后端（config.py 的 _RUNTIME_PARAM_LABELS），
              查不到就原样显示英文。⛔ 描述与 env 名不翻：那是给开发者看的。 */}
          <span className="text-neutral-500">{t(p.label)}</span>
          <span className="tabular-nums text-neutral-300">{p.value}</span>
        </span>
      ))}
      {cfg.trust_all && (
        <span className="ml-auto rounded border border-red-700/60 bg-red-950/40 px-1.5 py-0.5 text-red-300"
          title={t("Every node is treated as approved without review. Turn NERV_TRUST_ALL off before touching hardware.")}>
          ⚠ {t("NERV_TRUST_ALL is on — development only")}
        </span>
      )}
    </div>
  );
}
