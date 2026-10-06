import {apiFetch} from './http'
import {parseSpeakerReviewCapability,parseSpeakerReferencesResponse,parseSpeakerReviewRequest,parseSpeakerReview,parseSpeakerReviewsResponse} from './contracts'
import type {SpeakerReviewRequest} from './contracts'
function id(value:string){if(!/^[0-9a-f]{32}$/.test(value))throw new TypeError('Invalid speaker review identifier');return value}
function passagePath(book:string,chapter:number,passage:string){if(!Number.isInteger(chapter)||chapter<0||chapter>99)throw new TypeError('Invalid chapter');return `/api/audiobooks/${id(book)}/chapters/${chapter}/passages/${id(passage)}`}
export function getSpeakerCapability(signal?:AbortSignal){return apiFetch('/api/audiobooks/speaker-review/capability',{signal},parseSpeakerReviewCapability)}
export async function listSpeakerReferences(book:string,chapter:number,passage:string,revision:number,renderIdentity:string,signal?:AbortSignal){
  if(!Number.isSafeInteger(revision)||revision<1||!/^[0-9a-f]{64}$/.test(renderIdentity))throw new TypeError('Invalid passage identity')
  return (await apiFetch(`${passagePath(book,chapter,passage)}/speaker-references?revision=${revision}&render_identity=${renderIdentity}`,{signal},parseSpeakerReferencesResponse)).references??[]
}
export function startSpeakerReview(book:string,chapter:number,passage:string,body:SpeakerReviewRequest,signal?:AbortSignal){return apiFetch(`${passagePath(book,chapter,passage)}/speaker-review`,{method:'POST',signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(parseSpeakerReviewRequest(body))},parseSpeakerReview)}
export function getSpeakerReview(reviewId:string,signal?:AbortSignal){return apiFetch(`/api/audiobooks/speaker-review/${id(reviewId)}`,{signal},parseSpeakerReview)}
export function cancelSpeakerReview(reviewId:string,signal?:AbortSignal){return apiFetch(`/api/audiobooks/speaker-review/${id(reviewId)}/cancel`,{method:'POST',signal},parseSpeakerReview)}
export async function listSpeakerReviews(book:string,signal?:AbortSignal){return (await apiFetch(`/api/audiobooks/${id(book)}/speaker-reviews`,{signal},parseSpeakerReviewsResponse)).reviews??[]}
