export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

let unauthorizedCallback: (() => void) | null = null;

export function setUnauthorizedCallback(callback: () => void): void {
  unauthorizedCallback = callback;
}

async function errorMessage(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const data = await response.json().catch(() => null);
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  }
  const text = await response.text().catch(() => "");
  return text || `Request failed (${response.status})`;
}

export async function apiFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers ?? {});
  const response = await fetch(url, { ...options, credentials: "same-origin", headers });
  if (response.status === 401) unauthorizedCallback?.();
  if (!response.ok) {
    const message = await errorMessage(response);
    throw new ApiError(message, response.status);
  }
  return response;
}

export async function apiJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await apiFetch(url, options);
  return response.json() as Promise<T>;
}

function bodyJson<T>(method: string, url: string, body: unknown): Promise<T> {
  return apiJson<T>(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const postJson = <T>(url: string, body: unknown) => bodyJson<T>("POST", url, body);
export const putJson = <T>(url: string, body: unknown) => bodyJson<T>("PUT", url, body);
export const patchJson = <T>(url: string, body: unknown) => bodyJson<T>("PATCH", url, body);
export const del = <T>(url: string) => apiJson<T>(url, { method: "DELETE" });
