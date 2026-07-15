"use client";

import { useApp } from "@/contexts/AppContext";
import Sidebar, { type PageKey } from "./Sidebar";
import Header from "./Header";
import LoginPage from "@/components/LoginPage";
import Dashboard from "@/components/Dashboard";
import ScenarioManager from "@/components/ScenarioManager";
import DataManager from "@/components/DataManager";
import SchemaManager from "@/components/SchemaManager";
import SchemaOptimizationManager from "@/components/SchemaOptimizationManager";
import ConceptManager from "@/components/ConceptManager";
import DimensionGroupManager from "@/components/DimensionGroupManager";
import MetricManager from "@/components/MetricManager";
import ChartRulesManager from "@/components/ChartRulesManager";
import GlossaryManager from "@/components/GlossaryManager";
import SkillsManager from "@/components/SkillsManager";
import ActionsManager from "@/components/ActionsManager";
import WorkflowsManager from "@/components/WorkflowsManager";
import AlertRulesManager from "@/components/AlertRulesManager";
import UsersManager from "@/components/UsersManager";
import ExtractionLogs from "@/components/ExtractionLogs";
import SystemSettings from "@/components/SystemSettings";
import AuditLogs from "@/components/AuditLogs";
import ToastContainer from "@/components/ui/Toast";
import { useState } from "react";

const PAGE_COMPONENTS: Record<PageKey, React.ComponentType> = {
  "dashboard": Dashboard,
  "scenarios": ScenarioManager,
  "data": DataManager,
  "extraction-logs": ExtractionLogs,
  "schema": SchemaManager,
  "schema-optimization": SchemaOptimizationManager,
  "concepts": ConceptManager,
  "dimension-groups": DimensionGroupManager,
  "metrics": MetricManager,
  "chart-rules": ChartRulesManager,
  "glossary": GlossaryManager,
  "skills": SkillsManager,
  "actions": ActionsManager,
  "workflows": WorkflowsManager,
  "alert-rules": AlertRulesManager,
  "users": UsersManager,
  "settings": SystemSettings,
  "audit-logs": AuditLogs,
};

export default function AdminLayout() {
  const { token, sidebarCollapsed } = useApp();
  const [activePage, setActivePage] = useState<PageKey>("dashboard");

  if (!token) return <LoginPage />;

  const PageComponent = PAGE_COMPONENTS[activePage] || Dashboard;

  return (
    <div className="h-screen flex overflow-hidden bg-deloitte-mist">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <div className="flex-1 flex flex-col min-w-0">
        <Header activePage={activePage} />
        <main className="flex-1 overflow-y-auto p-6">
          <PageComponent />
        </main>
      </div>
      <ToastContainer />
    </div>
  );
}
