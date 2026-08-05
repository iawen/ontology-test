export function formatDateTime(value?: string | number | null) {
  if (value === null || value === undefined || value === "") return "-";

  const rawValue = String(value);
  const date = /^\d{10,13}$/.test(rawValue)
    ? new Date(Number(rawValue) * (rawValue.length === 10 ? 1000 : 1))
    : new Date(rawValue.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return rawValue;

  const pad = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
