// 공용 fetch 헬퍼 — FastAPI 에러 형식 {"detail": "..."} 처리 포함

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === 'string') detail = body.detail;
    } catch {
      // JSON 이 아닌 에러 응답은 기본 메시지 유지
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function getJSON<T>(url: string): Promise<T> {
  return fetch(url).then((res) => handle<T>(res));
}

export function postJSON<T>(url: string, body?: unknown): Promise<T> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((res) => handle<T>(res));
}

export function putJSON<T>(url: string, body: unknown): Promise<T> {
  return fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then((res) => handle<T>(res));
}

export function deleteJSON<T>(url: string): Promise<T> {
  return fetch(url, {
    method: 'DELETE',
  }).then((res) => handle<T>(res));
}
