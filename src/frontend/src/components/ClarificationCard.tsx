"use client";

import { useEffect, useState } from "react";
import type { ClarificationAnswer, ClarificationData, ClarificationQuestion } from "@/lib/types";

interface Props {
  data: ClarificationData;
  onSelect: (optionId: string, value: string) => void;
  onSubmitAnswers?: (answers: ClarificationAnswer[]) => void;
}

export default function ClarificationCard({ data, onSelect, onSubmitAnswers }: Props) {
  const persistedAnswers = data.submitted_answers || [];
  const [answers, setAnswers] = useState<Record<string, string>>(() => Object.fromEntries(
    persistedAnswers.map((answer) => [answer.requirement_id || answer.group_id, answer.option_value]),
  ));
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(
    persistedAnswers.map((answer) => [answer.requirement_id || answer.group_id, answer.selection_value || ""]),
  ));
  const submitted = data.status === "submitted";
  useEffect(() => {
    if (!submitted) return;
    setAnswers(Object.fromEntries(persistedAnswers.map((answer) => [answer.requirement_id || answer.group_id, answer.option_value])));
    setValues(Object.fromEntries(persistedAnswers.map((answer) => [answer.requirement_id || answer.group_id, answer.selection_value || ""])));
  }, [persistedAnswers, submitted]);
  const questions = (data.version || data.clarification_version || 0) >= 2 ? data.questions || [] : [];
  const questionKey = (question: ClarificationQuestion) => question.requirement_id || question.group_id;
  const ready = questions.length > 0 && questions.every((question) => {
    const key = questionKey(question);
    return answers[key] && (!question.requires_value || values[key]?.trim());
  });
  return (
    <div className="my-3 rounded-xl border border-amber-300/40 bg-amber-50/60 dark:bg-amber-900/20 dark:border-amber-700/40 overflow-hidden">
      <div className="px-4 py-3 border-b border-amber-200/50 dark:border-amber-700/30 flex items-center gap-2">
        <span className="text-lg">🤔</span>
        <span className="font-semibold text-amber-800 dark:text-amber-300 text-sm">
          需要确认
        </span>
      </div>
      <div className="px-4 py-3">
        <p className="text-sm text-amber-900 dark:text-amber-200 mb-3">{data.question}</p>
        {questions.length > 0 ? (
          <div className="space-y-4">
            {questions.map((question) => {
              const key = questionKey(question);
              return (
              <section key={key}>
                <p className="mb-2 text-xs font-semibold text-amber-800 dark:text-amber-300">{question.group_name}</p>
                {question.execution_unit_ids?.length ? <p className="mb-2 text-xs text-amber-700 dark:text-amber-400">影响分析项：{question.metric_ids.join("、") || question.execution_unit_ids.join("、")}</p> : null}
                {question.semantic_suggestions?.length ? <p className="mb-2 text-xs text-amber-700 dark:text-amber-400">已识别可能相关的输入，请确认对应口径。</p> : null}
                <div className="flex flex-wrap gap-2">
                  {question.options.map((option) => {
                    const value = option.value || option.id;
                    const selected = answers[key] === value;
                    return <button key={option.id} disabled={submitted} onClick={() => setAnswers((current) => ({ ...current, [key]: value }))}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${selected ? "border-deloitte-green bg-deloitte-green-light text-deloitte-ink" : "bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-700/50 text-amber-800 dark:text-amber-300 hover:bg-amber-100"}`}>{option.label}</button>;
                  })}
                </div>
                {question.requires_value && answers[key] && (
                  <input
                    value={values[key] || ""}
                    disabled={submitted}
                    onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value.toUpperCase() }))}
                    placeholder={answers[key] === "month" ? "例如：2026AP06" : answers[key] === "quarter" ? "例如：2026Q2" : "例如：2026"}
                    className="mt-2 w-full rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-deloitte-green focus:ring-2 focus:ring-deloitte-green/20 dark:border-amber-700/50 dark:bg-slate-800 dark:text-slate-100"
                  />
                )}
              </section>
              );
            })}
            <button disabled={!ready || submitted} onClick={() => onSubmitAnswers?.(questions.map((question) => {
              const key = questionKey(question);
              return { requirement_id: question.requirement_id, group_id: question.group_id, option_value: answers[key], selection_value: values[key]?.trim() || undefined };
            }))}
              className="rounded-lg bg-deloitte-green px-4 py-2 text-sm font-semibold text-deloitte-ink hover:bg-deloitte-green-dark hover:text-white disabled:cursor-not-allowed disabled:opacity-40">{submitted ? "已确认" : "继续查询"}</button>
          </div>
        ) : <div className="flex flex-wrap gap-2">{data.options.map((opt) => <button key={opt.id} onClick={() => onSelect(opt.id, opt.value || opt.label)} className="px-3 py-1.5 rounded-lg text-sm font-medium transition-all bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-700/50 text-amber-800 dark:text-amber-300 hover:bg-amber-100">{opt.label}</button>)}</div>}
      </div>
    </div>
  );
}
