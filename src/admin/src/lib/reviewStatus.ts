export type ReviewStatus = -1 | 0 | 1;

export function normalizeReviewStatus(value: unknown): ReviewStatus {
  if (value === true) return 1;
  if (value === false || value == null) return 0;
  const numericValue = Number(value);
  return numericValue === -1 || numericValue === 1 ? numericValue : 0;
}

export function reviewStatusLabel(value: unknown) {
  const status = normalizeReviewStatus(value);
  if (status === 1) return "已通过";
  if (status === -1) return "不通过";
  return "待审核";
}

export function reviewStatusClassName(value: unknown) {
  const status = normalizeReviewStatus(value);
  if (status === 1) return "bg-emerald-50 text-emerald-600";
  if (status === -1) return "bg-red-50 text-red-600";
  return "bg-slate-100 text-slate-500";
}
