import { expect, it } from 'vitest'
import { parseUpdateVideoProjectRequest } from '../../api/contracts'

it('requires the actual provider discriminator even when model members have default tags',()=>{
  expect(()=>parseUpdateVideoProjectRequest({revision:1,provider_config:{model_id:'google/veo-3.1-fast',size:'1280x720'}})).toThrow()
  expect(()=>parseUpdateVideoProjectRequest({revision:1,provider_config:{}})).toThrow()
  expect(parseUpdateVideoProjectRequest({revision:1,provider_config:{provider:'openrouter',model_id:'google/veo-3.1-fast',size:'1280x720'}}).provider_config?.provider).toBe('openrouter')
})
