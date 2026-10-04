import { describe, expect, it } from 'vitest'
import type { VideoShotDraft } from '../../api/contracts'
import { changeShotLength, characterLockIssue, duplicateShot, moveShot, shotProblem, splitShot, toShotDraft, videoWorkspaceSteps } from './videoWorkspace'
const first: VideoShotDraft = { id: 'a'.repeat(32), start_sec: 0, seconds: 8, prompt: 'A reviewed scene', seed: 12 }
const second: VideoShotDraft = { id: 'b'.repeat(32), start_sec: 8, seconds: 4, prompt: 'A second scene', seed: 13 }
describe('video storyboard edits', () => {
  it('exposes the approved project progression', () => { expect(videoWorkspaceSteps).toEqual(['song', 'direction', 'storyboard', 'preview', 'export']) })
  it('ripple length editing moves later shots without changing their direction', () => {
    const edited = changeShotLength([first, second], first.id, 12, true)
    expect(edited.map((shot) => shot.start_sec)).toEqual([0, 12]); expect(edited[1]?.prompt).toBe(second.prompt); expect(first.seconds).toBe(8)
  })
  it('fixed-position editing reports the overlapping shot locally', () => { expect(shotProblem(changeShotLength([first, second], first.id, 12, false), second.id, 30)).toBe('overlap') })
  it('splits a clip into supported generation lengths and new identity', () => {
    const edited = splitShot([first, second], first.id, 'c'.repeat(32))
    expect(edited.map((shot) => [shot.start_sec, shot.seconds])).toEqual([[0, 4], [4, 4], [8, 4]]); expect(edited[1]?.id).toBe('c'.repeat(32))
  })
  it('keeps the minimum generation clip intact', () => { expect(splitShot([{ ...first, seconds: 2 }], first.id, 'c'.repeat(32))).toEqual([{ ...first, seconds: 2 }]) })
  it('duplicates with a new seed and moves downstream time', () => {
    const edited = duplicateShot([first, second], first.id, 'c'.repeat(32)); expect(edited.map((shot) => shot.start_sec)).toEqual([0, 8, 16]); expect(edited[1]?.seed).toBe(13)
  })
  it('reordering repacks the timeline while retaining shot identities', () => { expect(moveShot([first, second], second.id, -1).map((shot) => [shot.id, shot.start_sec])).toEqual([[second.id, 0], [first.id, 4]]) })
  it('reports frame alignment and source boundaries before saving', () => {
    expect(shotProblem([{ ...first, start_sec: .1 }], first.id, 30)).toBe('frame_alignment'); expect(shotProblem([first, second], second.id, 10)).toBe('past_end'); expect(shotProblem([{ ...first, prompt: '   ' }], first.id, 30)).toBe('bad_prompt')
  })
  it('sends only editable fields instead of server-owned variants', () => {
    const draft = toShotDraft({ ...first, variants: [], approved_variant_id: null }); expect(draft).toEqual({ ...first, reference_id: null, reference_strength: .7, locked: false }); expect('variants' in draft).toBe(false)
  })
})

it('warns when character lock has no still and accepts one shared still', () => {
  const project = { track_id: null, mode: 'generated', character_lock: true, references: [] as { id: string }[] }
  expect(characterLockIssue(project, [first])).toBe('missing')
  const locked = { ...project, references: [{ id: 'f'.repeat(32) }] }
  expect(characterLockIssue(locked, [first, second])).toBe('')
  expect(characterLockIssue({ ...locked, character_lock: false }, [first])).toBe('')
  expect(characterLockIssue({ ...locked, track_id: 4 }, [first])).toBe('')
})
