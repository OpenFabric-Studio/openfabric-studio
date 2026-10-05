export const cloudMusicEn = {
  title: 'Cloud music', experimental: 'Experimental', intro: 'Generate with Lyria through OpenRouter and save the result to your track library.',
  limits: 'Cloud music quality and timing vary. A prompt can describe lyrics, style and duration; it does not guarantee an exact result. Seed control is shown only when advertised.',
  setup: 'Set up OpenRouter in Settings', model: 'Cloud music model', prompt: 'Describe the music', promptHint: 'Style, instrumentation, mood and any lyrics…', titleLabel: 'Track title', seed: 'Seed (optional)',
  retrySave: 'Save downloaded music to library (no new generation)',
  generate: 'Generate cloud music', refresh: 'Refresh models (free)', refreshJobs: 'Refresh history', noModels: 'No cached music models are available. Enable OpenRouter and refresh the catalog.',
  estimate: 'Estimated charge: ${cost}', estimateHint: 'Your prompt is sent to OpenRouter and the selected provider. This estimate is not a billing cap.',
  history: 'Cloud music history', empty: 'Completed music will appear here and in your library.', cancel: 'Stop tracking', cancelHint: 'Stopping tracking does not confirm remote cancellation or prevent billing.',
  loadFailed: 'Could not load cloud music. Check provider setup and refresh.', actionFailed: 'Could not complete the cloud music action. Refresh history before starting another paid request.',
  expired: 'The quote expired. Review a new estimate.', failed: 'Generation did not complete. Review provider activity before trying again.',
  states: { queued: 'Queued', running: 'Generating', done: 'Saved to library', failed: 'Failed', submission_unknown: 'Submission outcome unknown', canceled_tracking: 'Tracking stopped', interrupted: 'Interrupted before completion' },
}
