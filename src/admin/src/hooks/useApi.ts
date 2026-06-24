import { useCallback } from "react";

export function useApi(token: string) {
  const api = useCallback(
    async (url: string, opts?: RequestInit) => {
      const headers: Record<string, string> = {
        ...((opts?.headers as Record<string, string>) || {}),
      };
      if (opts?.body instanceof FormData) {
        delete headers["Content-Type"];
      } else {
        headers["Content-Type"] = "application/json";
      }
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(url, { ...opts, headers });
      if (res.status === 401) throw new Error("未授权");
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `请求失败 (${res.status})`);
      }
      return res.json();
    },
    [token],
  );
  return api;
}
