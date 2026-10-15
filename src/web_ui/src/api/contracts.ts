/** Read-only web console contracts. All timestamp values are RFC 3339 with offset. */
export type DataQuality = 'GOOD' | 'STALE' | 'BAD' | 'UNKNOWN'
export type HealthState = 'healthy' | 'unavailable' | 'unknown'
export interface Measurement {
  value: number | null
  unit: string
  timestamp: string | null
  quality: DataQuality
}
export interface ServiceHealth {
  id: string
  name: string
  state: HealthState
  detail: string
  checkedAt?: string
}
export interface RuntimeInfo {
  controllerVersion: string
  apiVersion: string
  buildRevision: string | null
}
export interface StationSnapshot {
  timestamp: string
  quality: DataQuality
  version: string | null
  mode: string | null
  authority: string | null
  strategy: string | null
  activePower: Measurement
  targetPower?: Measurement
  reactivePower: Measurement
  voltage: Measurement
  frequency: Measurement
  ratedPower: Measurement
  availablePower: Measurement
  periodMs: Measurement
  periodDeviationMs: Measurement
}
export interface ControlFunction {
  id: string
  name: string
  enabled: boolean | null
  responseState: string
  algorithm: string | null
  uptimeSeconds: number | null
  target: Measurement
  actual: Measurement
  constraint: string | null
}
export interface CapabilityReason {
  deviceId: string
  category: string
  reason: string
  limitedPower: Measurement
  since: string | null
}
export interface CapabilitySnapshot {
  id: string
  label: string
  measurement: Measurement
  summary: string
  reasons: CapabilityReason[]
  timestamp: string
}
export interface DeviceSnapshot {
  id: string
  type: string
  availablePower: Measurement
  activePower: Measurement
  targetPower: Measurement
  participation: boolean | null
  status: string
  weight: number | null
}
export type ExecutionState = 'PENDING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN' | 'REJECTED'
export interface AuditEvent {
  id: string
  timestamp: string
  kind: string
  objectId: string
  result: ExecutionState
  commandId: string | null
  detail: string
}
export interface HistoryPoint {
  timestamp: string
  value: number | null
  quality: DataQuality
}
export interface HistorySeries {
  signalId: string
  unit: string
  points: HistoryPoint[]
}
export interface ControllerConfiguration {
  version: string
  revision: string
  updatedAt: string
  station: Record<string, unknown>
  controls: Record<string, unknown>
  coordination: Record<string, unknown>
}
export interface ConfigValidation {
  valid: boolean
  errors: Array<{ path: string; message: string }>
}
export interface OfflineExperiment {
  id: string
  createdAt: string
  algorithmId: string
  parameters: Record<string, number>
  status: 'DRAFT' | 'EVALUATED' | 'REJECTED'
  metrics: Record<string, number | null>
}
export interface PipelineStage {
  id: string
  label: string
  algorithm: string | null
  parameters: Record<string, unknown>
  inputs: string[]
  outputs: string[]
  status: 'READY' | 'BLOCKED' | 'UNKNOWN'
}
export interface PipelineSnapshot {
  timestamp: string
  quality: DataQuality
  stages: PipelineStage[]
}
export interface CommandAudit {
  commandId: string
  state: ExecutionState
  timestamp: string
  deviceResults: Array<{ deviceId: string; state: ExecutionState; reason: string | null }>
}
/** Commands are intentionally absent: read-side contracts must not bypass controller interlocks. */
