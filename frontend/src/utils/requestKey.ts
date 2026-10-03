/** Random idempotency key, including self-hosted HTTP browser contexts. */
export function createRequestKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}
