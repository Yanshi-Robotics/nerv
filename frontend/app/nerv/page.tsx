// /nerv 独立路由：薄壳，渲染同款 NervDashboard 组件（整页版）。
// 主界面里是内嵌版（embedded），逻辑同一份，见 components/NervDashboard.tsx。
import NervDashboard from "@/components/NervDashboard";

export default function NervPage() {
  return <NervDashboard />;
}
