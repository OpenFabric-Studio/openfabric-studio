import { afterEach, expect, it, vi } from 'vitest'
import { songFromBeatCapabilities } from './songFromBeat'
const row = { mode: 'ace_complete', base_model: 'acestep-v15-base', installed: false, weights_ready: false, ready: false, proof_status: 'source_verified', source_commit: 'a'.repeat(40), proof_sha256: 'b'.repeat(64), runtime_verified: false, output_semantics: 'generated_full_mix', notes: 'Runtime unverified.' }
afterEach(() => vi.restoreAllMocks())
it('validates both source task identities and forwards abort ownership', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ modes: [row, { ...row, mode: 'ace_lego' }] }))).mockResolvedValueOnce(new Response(JSON.stringify({ modes: [row, row] })))
  const signal = new AbortController().signal
  await expect(songFromBeatCapabilities(signal)).resolves.toMatchObject({ modes: [row, { ...row, mode: 'ace_lego' }] })
  expect(fetch).toHaveBeenCalledWith('/api/song-from-beat/capabilities', { signal })
  await expect(songFromBeatCapabilities()).rejects.toThrow('Mismatched song task capabilities')
})
