import raw from './mashupFixture.json'
import rawJob from './mashupJobFixture.json'
import { parseMashupJob, parseMashupState } from '../api/generated'
export const mashupState = () => parseMashupState(structuredClone(raw))
export const mashupJob = () => parseMashupJob(structuredClone(rawJob))
