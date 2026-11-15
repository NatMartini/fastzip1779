export type Account = { id: string; email: string; role: "user" | "admin" };
export type Job = {
  id: string;
  filename: string;
  media_type: string;
  preset: string;
  status: "QUEUED" | "PROCESSING" | "SUCCEEDED" | "FAILED" | "EXPIRED";
  original_size: number;
  output_size: number | null;
  savings_percent: number | null;
  original_is_smaller: boolean;
  attempts: number;
  processing_seconds: number | null;
  created_at: string;
  expires_at: string;
  error: string | null;
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
let token = "";
export async function refreshCsrf() {
  const response = await fetch("/api/auth/csrf", {
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("Unable to connect. Please try again.");
  token = (await response.json()).csrf_token;
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  if (method !== "GET" && !token) await refreshCsrf();
  const headers = new Headers(init.headers);
  if (method !== "GET") headers.set("X-CSRF-Token", token);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api" + path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail =
      typeof body.detail === "string" ? body.detail : body.detail?.[0]?.msg;
    if (response.status === 403) token = "";
    throw new ApiError(
      detail || "Something went wrong. Please try again.",
      response.status,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const bytes = (value: number) =>
  value < 1024 * 1024
    ? `${(value / 1024).toFixed(0)} KB`
    : `${(value / 1024 / 1024).toFixed(1)} MB`;
