import type { Measurement, StationSnapshot } from './contracts'

const parsedAge = Number(import.meta.env.VITE_LIVE_MAX_AGE_MS ?? 5000)
const maxAgeMs = Number.isFinite(parsedAge) && parsedAge > 0 ? parsedAge : 5000

/** A responsive HTTP endpoint alone never establishes the validity of a control measurement. */
export function isFreshTimestamp(timestamp: string | null | undefined, now = Date.now()): boolean {
  if (!timestamp) return false
  const sampleTime = Date.parse(timestamp)
  const age = now - sampleTime
  return Number.isFinite(sampleTime) && age >= -2000 && age <= maxAgeMs
}

export function isUsableMeasurement(measurement: Measurement | null | undefined): measurement is Measurement & { value: number } {
  return measurement != null &&
    measurement.quality === 'GOOD' &&
    measurement.value !== null &&
    Number.isFinite(measurement.value) &&
    isFreshTimestamp(measurement.timestamp)
}

export function isUsableStation(snapshot: StationSnapshot | null | undefined): snapshot is StationSnapshot {
  return snapshot != null &&
    snapshot.quality === 'GOOD' &&
    isFreshTimestamp(snapshot.timestamp)
}
