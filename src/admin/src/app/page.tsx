"use client";

import { AppProvider } from "@/contexts/AppContext";
import AdminLayout from "@/components/layout/AdminLayout";

export default function Home() {
  return (
    <AppProvider>
      <AdminLayout />
    </AppProvider>
  );
}
