// /session-logs 独立路由：薄壳，渲染同款 SessionLogsView 组件（整页版）。
// 主界面里是内嵌版（embedded），逻辑同一份，见 components/SessionLogsView.tsx。
import SessionLogsView from "@/components/SessionLogsView";

export default function SessionLogsPage() {
  return <SessionLogsView />;
}
