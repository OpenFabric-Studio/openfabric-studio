/** Keep archived model/reference choices distinguishable without displaying full opaque IDs. */
export function shortVoiceId(id: string): string {
  return id.length <= 12 ? id : `${id.slice(0, 8)}…${id.slice(-4)}`
}
