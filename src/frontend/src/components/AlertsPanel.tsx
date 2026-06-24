"use client";
interface Props { data: { type: "alerts"; class_name: string; rows: Record<string,unknown>[]; total: number; columns: string[]; }; }
const LM: Record<string,{c:string;b:string;i:string}> = { 高:{c:"text-rose-500",b:"bg-rose-500/10",i:"🔴"}, 中:{c:"text-amber-500",b:"bg-amber-500/10",i:"🟡"}, 低:{c:"text-cyan-500",b:"bg-cyan-500/10",i:"🔵"} };
export default function AlertsPanel({ data }: Props) {
  const { rows, total, columns } = data;
  const lk = columns.find((k) => ["anomaly_level","level"].includes(k)) || "";
  const tk = columns.find((k) => ["anomaly_type","type"].includes(k)) || "";
  const dk = columns.find((k) => ["anomaly_desc","description"].includes(k)) || "";
  const sk = columns.find((k) => ["anomaly_status","status"].includes(k)) || "";
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <div className="glass-card p-4 text-center"><div className="text-xs mb-1" style={{color:"var(--text-muted)"}}>预警总数</div><div className="text-lg font-bold text-rose-500">{total}</div></div>
        <div className="glass-card p-4 text-center"><div className="text-xs mb-1" style={{color:"var(--text-muted)"}}>高级预警</div><div className="text-lg font-bold text-rose-500">{rows.filter((r) => String(r[lk]) === "高").length}</div></div>
        <div className="glass-card p-4 text-center"><div className="text-xs mb-1" style={{color:"var(--text-muted)"}}>未处理</div><div className="text-lg font-bold text-amber-500">{rows.filter((r) => String(r[sk]) === "未处理").length}</div></div>
      </div>
      <div className="space-y-2">{rows.slice(0,10).map((row, i) => {
        const lv = String(row[lk] || "低"); const cfg = LM[lv] || LM["低"];
        return (<div key={i} className="glass-card p-4 flex items-start gap-3 animate-in" style={{animationDelay:`${i*60}ms`}}>
          <div className={"w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 " + cfg.b}><span className="text-sm">{cfg.i}</span></div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className={"text-xs font-medium px-2 py-0.5 rounded-full " + cfg.b + " " + cfg.c}>{lv}</span>
              {tk && <span className="text-xs" style={{color:"var(--text-muted)"}}>{String(row[tk])}</span>}
              {sk && <span className={"text-xs px-2 py-0.5 rounded-full " + (String(row[sk])==="未处理"?"bg-rose-500/10 text-rose-500":String(row[sk])==="已处理"?"bg-emerald-500/10 text-emerald-500":"bg-amber-500/10 text-amber-500")}>{String(row[sk])}</span>}
            </div>
            {dk && <div className="text-sm line-clamp-2" style={{color:"var(--text-secondary)"}}>{String(row[dk])}</div>}
          </div>
        </div>);
      })}</div>
    </div>
  );
}
