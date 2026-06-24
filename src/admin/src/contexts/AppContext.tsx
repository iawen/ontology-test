"use client";

import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import type { Scenario } from "@/lib/types";

/* ────── Toast ────── */
export interface ToastItem {
  id: string;
  type: "success" | "error" | "info" | "warning";
  message: string;
}

interface AppContextType {
  /* auth */
  token: string;
  setToken: (t: string) => void;
  logout: () => void;

  /* scenario — 纯前端用户偏好，独立于后端 is_active */
  scenarios: Scenario[];
  setScenarios: (s: Scenario[]) => void;
  activeScenario: string;
  setActiveScenario: (id: string) => void;
  /** 登录后自动加载场景列表 */
  loadScenarios: () => Promise<void>;

  /* sidebar */
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;

  /* toast */
  toasts: ToastItem[];
  addToast: (type: ToastItem["type"], message: string) => void;
  removeToast: (id: string) => void;
}

const AppContext = createContext<AppContextType | null>(null);

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState("");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [activeScenario, setActiveScenarioState] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const scenariosLoadedRef = useRef(false);

  /* ── token ── */
  const setToken = useCallback((t: string) => {
    setTokenState(t);
    if (t) localStorage.setItem("admin_token", t);
    else localStorage.removeItem("admin_token");
  }, []);

  const logout = useCallback(() => {
    setToken("");
    setScenarios([]);
    setActiveScenarioState("");
    scenariosLoadedRef.current = false;
    localStorage.removeItem("admin_token");
    localStorage.removeItem("admin_active_scenario");
  }, []);

  /* ── 恢复 token ── */
  useEffect(() => {
    const saved = localStorage.getItem("admin_token");
    if (saved) setTokenState(saved);
  }, []);

  /* ── activeScenario：纯前端用户偏好，持久化到 localStorage ── */
  const setActiveScenario = useCallback((id: string) => {
    setActiveScenarioState(id);
    if (id) localStorage.setItem("admin_active_scenario", id);
  }, []);

  /** 恢复用户上次选择的场景 */
  const restoreActiveScenario = useCallback((loaded: Scenario[]) => {
    const saved = localStorage.getItem("admin_active_scenario");
    if (saved && loaded.some((s) => s.id === saved)) {
      setActiveScenarioState(saved);
    } else if (loaded.length > 0) {
      setActiveScenarioState(loaded[0].id);
    }
  }, []);

  /* ── 加载场景列表（登录后只调用一次） ── */
  const loadScenarios = useCallback(async () => {
    if (!token || scenariosLoadedRef.current) return;
    try {
      const res = await fetch("/api/admin/scenarios", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) return;
      const d = await res.json();
      setScenarios(d);
      restoreActiveScenario(d);
      scenariosLoadedRef.current = true;
    } catch {
      /* 静默失败 */
    }
  }, [token, restoreActiveScenario]);

  /* ── 登录后自动加载场景 ── */
  useEffect(() => {
    if (token) loadScenarios();
  }, [token, loadScenarios]);

  /* ── toast ── */
  const addToast = useCallback((type: ToastItem["type"], message: string) => {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <AppContext.Provider
      value={{
        token, setToken, logout,
        scenarios, setScenarios,
        activeScenario, setActiveScenario, loadScenarios,
        sidebarCollapsed, setSidebarCollapsed,
        toasts, addToast, removeToast,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}
