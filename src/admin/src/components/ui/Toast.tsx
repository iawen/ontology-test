"use client";
import { useApp, type ToastItem } from "@/contexts/AppContext";

const ICONS: Record<ToastItem["type"], string> = { success: "✓", error: "✕", info: "ℹ", warning: "⚠" };
const COLORS: Record<ToastItem["type"], string> = {
  success: "bg-emerald-50 border-emerald-200 text-emerald-800",
  error: "bg-red-50 border-red-200 text-red-800",
  info: "bg-blue-50 border-blue-200 text-blue-800",
  warning: "bg-amber-50 border-amber-200 text-amber-800",
};
const ICON_BG: Record<ToastItem["type"], string> = {
  success: "bg-emerald-500", error: "bg-red-500", info: "bg-blue-500", warning: "bg-amber-500",
};

export default function ToastContainer() {
  const { toasts, removeToast } = useApp();
  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div key={t.id} className={`flex items-start gap-3 px-4 py-3 rounded-lg border shadow-lg ${COLORS[t.type]}`}>
          <span className={`flex-shrink-0 w-5 h-5 rounded-full text-white text-xs flex items-center justify-center ${ICON_BG[t.type]}`}>{ICONS[t.type]}</span>
          <p className="text-sm flex-1">{t.message}</p>
          <button onClick={() => removeToast(t.id)} className="flex-shrink-0 opacity-60 hover:opacity-100">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M12 4L4 12M4 4l8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>
          </button>
        </div>
      ))}
    </div>
  );
}
