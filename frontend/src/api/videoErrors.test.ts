// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { ApiError } from './http'
import { videoErrorText, videoRequestError } from './videos'
import { setLocale } from '../i18n'

beforeEach(() => setLocale('en'))
it('keeps known API error codes for translation at the view boundary', () => {
  expect(videoRequestError(new ApiError('source_changed', 409))).toBe('source_changed')
  expect(videoErrorText('source_changed')).toContain('source song changed')
})
it.each([new Error('/private/library/model.safetensors'), new ApiError('Traceback /private/library', 500)])('sanitizes internal request failures before they reach the view', cause => {
  const code = videoRequestError(cause)
  expect(code).toBe('unknown')
  expect(videoErrorText(code)).toBe('The video could not be created.')
})
it('does not expose unknown job error codes or raw details', () => {
  expect(videoErrorText('/private/model/path')).toBe('The video could not be created.')
  expect(videoErrorText('source_changed', 'Traceback secret')).not.toContain('secret')
})
