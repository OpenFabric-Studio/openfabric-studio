import { apiFetch } from './http'
import { parseOpenRouterStatus, parseOpenRouterConnection, parseOpenRouterCatalog, parseOpenRouterQuote, parseOpenRouterReceipts } from './generated'
import type { OpenRouterStatus, OpenRouterSettingsRequest, OpenRouterKeyRequest, OpenRouterConnection, OpenRouterCatalog, OpenRouterQuoteRequest, OpenRouterQuote, OpenRouterReceipts } from './generated'
function json(body: unknown, method: string, signal?: AbortSignal): RequestInit { return { method, signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } }
export const getProviderStatus = (signal?: AbortSignal): Promise<OpenRouterStatus> => apiFetch('/api/openrouter/settings', { signal }, parseOpenRouterStatus)
export const saveProviderSettings = (body: OpenRouterSettingsRequest, signal?: AbortSignal): Promise<OpenRouterStatus> => apiFetch('/api/openrouter/settings', json(body, 'PUT', signal), parseOpenRouterStatus)
export const saveProviderKey = (body: OpenRouterKeyRequest, signal?: AbortSignal): Promise<OpenRouterStatus> => apiFetch('/api/openrouter/credential', json(body, 'POST', signal), parseOpenRouterStatus)
export const removeProviderKey = (signal?: AbortSignal): Promise<OpenRouterStatus> => apiFetch('/api/openrouter/credential', { method: 'DELETE', signal }, parseOpenRouterStatus)
export const checkProviderConnection = (signal?: AbortSignal): Promise<OpenRouterConnection> => apiFetch('/api/openrouter/connection', { method: 'POST', signal }, parseOpenRouterConnection)
export const getProviderCatalog = (signal?: AbortSignal): Promise<OpenRouterCatalog> => apiFetch('/api/openrouter/catalog', { signal }, parseOpenRouterCatalog)
export const refreshProviderCatalog = (signal?: AbortSignal): Promise<OpenRouterCatalog> => apiFetch('/api/openrouter/catalog/refresh', { method: 'POST', signal }, parseOpenRouterCatalog)
export const quoteProvider = (body: OpenRouterQuoteRequest, signal?: AbortSignal): Promise<OpenRouterQuote> => apiFetch('/api/openrouter/quote', json(body, 'POST', signal), parseOpenRouterQuote)
export const getProviderReceipts = (signal?: AbortSignal): Promise<OpenRouterReceipts> => apiFetch('/api/openrouter/requests', { signal }, parseOpenRouterReceipts)
