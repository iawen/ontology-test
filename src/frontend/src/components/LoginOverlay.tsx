"use client";

import React from "react";

interface LoginOverlayProps {
  username: string;
  setUsername: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  error: string;
  onLogin: () => void;
}

export default function LoginOverlay({
  username,
  setUsername,
  password,
  setPassword,
  error,
  onLogin,
}: LoginOverlayProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-white dark:bg-slate-900 rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-800 p-8">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-slate-800 dark:text-slate-100">Ontology AI 控制台</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">请输入凭证以建立认知会话</p>
        </div>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">用户名</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户账户"
              className="w-full px-3.5 py-2.5 rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-950 text-sm font-medium text-slate-900 dark:text-slate-100 placeholder:font-normal placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-1.5">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full px-3.5 py-2.5 rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-950 text-sm font-medium text-slate-900 dark:text-slate-100 placeholder:font-normal placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              onKeyDown={(e) => e.key === "Enter" && onLogin()}
            />
          </div>
          {error && <p className="text-xs text-red-500 font-medium animate-shake">⚠️ {error}</p>}
          <button
            onClick={onLogin}
            className="w-full py-2.5 mt-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-sm rounded-xl transition-colors cursor-pointer shadow-md shadow-indigo-600/10"
          >
            安全登录
          </button>
        </div>
      </div>
    </div>
  );
}