import { parseReplacementCapabilities, parseReplacementPlanResponse } from '../api/generated'
import fixture from './replacementFixture.json'
export function replacementPlan() {
  return parseReplacementPlanResponse(JSON.parse(JSON.stringify(fixture)))
}
export function replacementCapabilities() { return parseReplacementCapabilities({ separation: { options: [{ id: 'fast', available: true }, { id: 'high', available: false }, { id: 'roformer', available: false }] } }) }
