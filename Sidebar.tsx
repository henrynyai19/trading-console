import { LayoutDashboard, LineChart, Target, History, FlaskConical, Settings } from "lucide-react";
const ITEMS = [[LayoutDashboard, "Dashboard"], [LineChart, "Market Analysis"], [Target, "Trade Setups"], [History, "Signal History"], [FlaskConical, "Backtesting"], [Settings, "Settings"]] as const;
export default function Sidebar() {
  return (
    <nav aria-label="Main" className="hidden w-52 shrink-0 flex-col md:flex gap-1 border-r border-edge bg-panel p-3">
      <p className="mb-3 px-2 font-semibold">Trading Console</p>
      {ITEMS.map(([Icon, label], i) => (
        <a key={label} href="#" aria-current={i === 0 ? "page" : undefined}
          className={`flex items-center gap-2 rounded px-2 py-2 text-sm ${i === 0 ? "bg-edge" : "text-mute hover:bg-edge"}`}>
          <Icon size={16} />{label}
        </a>
      ))}
    </nav>
  );
}
