export interface SearchHit {
  readonly id: string;
  readonly type: string;
  readonly title: string;
}

export interface SearchResponse {
  readonly hits: readonly SearchHit[];
}

export type FetchLike = typeof fetch;

const SEARCH_HEADERS = {
  Accept: "application/json",
  "Content-Type": "application/json",
  "X-Second-Brain-Request": "search-v1",
} as const;

function isSearchResponse(value: unknown): value is SearchResponse {
  if (typeof value !== "object" || value === null || !("hits" in value)) {
    return false;
  }
  return Array.isArray(value.hits);
}

export async function searchNotes(
  query: string,
  fetcher: FetchLike = window.fetch.bind(window),
): Promise<SearchResponse> {
  const response = await fetcher("/api/search", {
    method: "POST",
    headers: SEARCH_HEADERS,
    body: JSON.stringify({ query, limit: 20 }),
  });

  if (!response.ok) {
    throw new Error("Search request failed");
  }

  const payload: unknown = await response.json();
  if (!isSearchResponse(payload)) {
    throw new Error("Search response is invalid");
  }
  return payload;
}
