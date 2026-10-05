import { afterEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProvidersStore } from './providers'
import { getProviderStatus } from '../api/openrouter'
import type { OpenRouterStatus } from '../api/generated'
vi.mock('../api/openrouter',()=>({getProviderStatus:vi.fn()}))
afterEach(()=>{setActivePinia(undefined);vi.clearAllMocks()})
it('never labels a configured key as a verified live connection',()=>{setActivePinia(createPinia());const store=useProvidersStore();expect(store.state).toBe('unknown');store.accept({enabled:true,credential_configured:true,credential_source:'session',secure_storage_available:false});expect(store.state).toBe('configured');store.accept({enabled:false,credential_configured:true,credential_source:'session',secure_storage_available:false});expect(store.state).toBe('disabled')})
it('ignores a stale poll after a newer settings mutation',async()=>{let finish:(v:OpenRouterStatus)=>void=()=>{throw new Error('not ready')};vi.mocked(getProviderStatus).mockReturnValueOnce(new Promise(r=>{finish=r}));setActivePinia(createPinia());const store=useProvidersStore();const poll=store.refresh();store.accept({enabled:true,credential_configured:true,credential_source:'session',secure_storage_available:false});finish({enabled:false,credential_configured:false,credential_source:'none',secure_storage_available:false});await poll;expect(store.state).toBe('configured');expect(vi.mocked(getProviderStatus).mock.calls[0]?.[0]?.aborted).toBe(true)})
