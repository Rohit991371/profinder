export function canMarkSubmitted(itemStatuses: string[]): boolean {
  return itemStatuses.length > 0 && itemStatuses.every((status) => status === "approved");
}
