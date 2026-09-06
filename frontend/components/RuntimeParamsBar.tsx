"use client";
import { useEffect, useState } from "react";

import { getRuntimeConfig, type RuntimeConfig } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// 运行上限：决定一个回合能跑多深、多久、记得多少的几个闸。住在左侧栏最底下，小字、灰色。
// ⛔ 值 / env 名 / 说明全部从后端现读（GET /api/config → config.Settings），
//    前端一个数字都不写死——写两份必然出现"网页显示 60、.env 里是 8"的对不上账。
// 只读：改参数走 .env + 重启，config.py 保持单一来源。
// 同一块里还挂着 NERV_TRUST_ALL 的告警：它开着时所有节点免审——那必须一直显眼。
export default function RuntimeParamsBar() {
  const { t } = useI18n();
  const [cfg, setCfg] = useState<RuntimeConfig | null>(null);
  useEffect(() => {
    getRuntimeConfig().then(setCfg).catch(() => {});
  }, []);
  if (!cfg) return null;
  return (
    <div className="space-y-0.5 px-3 py-2 text-[10px] leading-snug text-neutral-500">
      <div className="uppercase tracking-wide text-neutral-600">{t("Runtime limits")}</div>
      {cfg.params.map((p) => (
        <div key={p.key} className="flex items-baseline justify-between gap-2" title={`${p.env}\n\n${p.description}`}>
          {/* ⭐ 后端送英文正典，界面在这里翻。标签来自后端（config.py 的 _RUNTIME_PARAM_LABELS），
              查不到就原样显示英文。⛔ 描述与 env 名不翻：那是给开发者看的。 */}
          <span className="truncate">{t(p.label)}</span>
          <span className="shrink-0 tabular-nums text-neutral-400">{p.value}</span>
        </div>
      ))}
      {cfg.trust_all && (
        <div className="mt-1 rounded border border-red-700/60 bg-red-950/40 px-1.5 py-0.5 text-red-300"
          title={t("Every node is treated as approved without review. Turn NERV_TRUST_ALL off before touching hardware.")}>
          ⚠ {t("NERV_TRUST_ALL is on — development only")}
        </div>
      )}
    </div>
  );
}
