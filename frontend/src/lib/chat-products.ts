export function orderChatProducts<T extends { key: string }>(
  entries: readonly T[],
  shownIds?: readonly string[] | null,
): T[] {
  const byId = new Map<string, T>();
  for (const entry of entries) {
    if (!byId.has(entry.key)) byId.set(entry.key, entry);
  }
  if (shownIds == null) return [...byId.values()].slice(0, 3);
  return [...new Set(shownIds)].slice(0, 3).flatMap((id) => {
    const item = byId.get(id);
    return item ? [item] : [];
  });
}
