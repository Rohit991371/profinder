export const DEFAULT_ADMIN_PAGE_SIZE = 20;
export const MAX_ADMIN_PAGE_SIZE = 100;

export type AdminPagination = {
  page: number;
  pageSize: number;
};

export function parseAdminPagination(params: URLSearchParams): AdminPagination {
  const requestedPage = Number.parseInt(params.get("page") || "1", 10);
  const requestedPageSize = Number.parseInt(
    params.get("pageSize") || String(DEFAULT_ADMIN_PAGE_SIZE),
    10,
  );

  return {
    page: Number.isFinite(requestedPage) ? Math.max(1, requestedPage) : 1,
    pageSize: Number.isFinite(requestedPageSize)
      ? Math.min(MAX_ADMIN_PAGE_SIZE, Math.max(1, requestedPageSize))
      : DEFAULT_ADMIN_PAGE_SIZE,
  };
}
