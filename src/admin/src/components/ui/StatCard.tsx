"use client";

interface Props {
  icon: string;
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}

export default function StatCard({ icon, label, value, sub, color = "bg-indigo-50 text-indigo-600" }: Props) {
  return (
    <div className="card p-5 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide">{label}</p>
          <p className="text-2xl font-bold text-slate-800 mt-1">{value}</p>
          {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
        </div>
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-lg ${color}`}>{icon}</div>
      </div>
    </div>
  );
}
