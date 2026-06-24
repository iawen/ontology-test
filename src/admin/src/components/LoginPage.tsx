"use client";
import { useState } from "react";
import { useApp } from "@/contexts/AppContext";

export default function LoginPage() {
  const { setToken, addToast } = useApp();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (!username || !password) { addToast("warning", "请输入用户名和密码"); return; }
    setLoading(true);
    try {
      const r = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const d = await r.json();
      if (d.token) { setToken(d.token); addToast("success", "登录成功"); }
      else { addToast("error", d.detail || "登录失败"); }
    } catch { addToast("error", "网络错误，请重试"); }
    finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-indigo-50">
      <div className="w-full max-w-md mx-4">
        <div className="card p-8 shadow-xl">
          <div className="text-center mb-8">
            <div className="w-14 h-14 rounded-2xl bg-indigo-600 flex items-center justify-center text-white text-2xl mx-auto mb-4">O</div>
            <h1 className="text-xl font-bold text-slate-800">本体助手管理平台</h1>
            <p className="text-sm text-slate-400 mt-1">Ontology Assistant Admin</p>
          </div>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">用户名</label>
              <input value={username} onChange={(e) => setUsername(e.target.value)} className="w-full" placeholder="请输入用户名" />
            </div>
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1.5">密码</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleLogin()} className="w-full" placeholder="请输入密码" />
            </div>
            <button onClick={handleLogin} disabled={loading} className="btn-primary w-full flex items-center justify-center gap-2">
              {loading && <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
              {loading ? "登录中..." : "登 录"}
            </button>
          </div>
          <p className="text-xs text-center text-slate-300 mt-6">默认账号: admin / admin123</p>
        </div>
      </div>
    </div>
  );
}
