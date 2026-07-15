"use client";

import { useState } from "react";
import type { ClarificationAnswer, ClarificationData } from "@/lib/types";

interface Props {
  data: ClarificationData;
  onSelect: (optionId: string, value: string) => void;
  onSubmitAnswers?: (answers: ClarificationAnswer[]) => void;
}

export default function ClarificationCard({ data, onSelect, onSubmitAnswers }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const questions = data.version === 2 ? data.questions || [] : [];
  const ready = questions.length > 0 && questions.every((question) => answers[question.group_id] && (!question.requires_value || values[question.group_id]?.trim()));
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
            {questions.map((question) => (
              <section key={question.group_id}>
                <p className="mb-2 text-xs font-semibold text-amber-800 dark:text-amber-300">{question.group_name}</p>
                <div className="flex flex-wrap gap-2">
                  {question.options.map((option) => {
                    const value = option.value || option.id;
                    const selected = answers[question.group_id] === value;
                    return <button key={option.id} onClick={() => setAnswers((current) => ({ ...current, [question.group_id]: value }))}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${selected ? "border-deloitte-green bg-deloitte-green-light text-deloitte-ink" : "bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-700/50 text-amber-800 dark:text-amber-300 hover:bg-amber-100"}`}>{option.label}</button>;
                  })}
                </div>
                {question.requires_value && answers[question.group_id] && (
                  <input
                    value={values[question.group_id] || ""}
                    onChange={(event) => setValues((current) => ({ ...current, [question.group_id]: event.target.value.toUpperCase() }))}
                    placeholder={answers[question.group_id] === "month" ? "例如：2026AP06" : answers[question.group_id] === "quarter" ? "例如：2026Q2" : "例如：2026"}
                    className="mt-2 w-full rounded-lg border border-amber-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-deloitte-green focus:ring-2 focus:ring-deloitte-green/20 dark:border-amber-700/50 dark:bg-slate-800 dark:text-slate-100"
                  />
                )}
              </section>
            ))}
            <button disabled={!ready} onClick={() => onSubmitAnswers?.(Object.entries(answers).map(([group_id, option_value]) => ({ group_id, option_value, selection_value: values[group_id]?.trim() || undefined })))}
              className="rounded-lg bg-deloitte-green px-4 py-2 text-sm font-semibold text-deloitte-ink hover:bg-deloitte-green-dark hover:text-white disabled:cursor-not-allowed disabled:opacity-40">继续查询</button>
          </div>
        ) : <div className="flex flex-wrap gap-2">{data.options.map((opt) => <button key={opt.id} onClick={() => onSelect(opt.id, opt.value || opt.label)} className="px-3 py-1.5 rounded-lg text-sm font-medium transition-all bg-white dark:bg-slate-800 border border-amber-200 dark:border-amber-700/50 text-amber-800 dark:text-amber-300 hover:bg-amber-100">{opt.label}</button>)}</div>}
      </div>
    </div>
  );
}
