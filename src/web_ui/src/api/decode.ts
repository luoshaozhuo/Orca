import type {
  AuditEvent, CapabilityReason, CapabilitySnapshot, CommandAudit, ConfigValidation,
  ControllerConfiguration, ControlFunction, DataQuality, DeviceSnapshot, ExecutionState,
  HistoryPoint, HistorySeries, Measurement, OfflineExperiment, PipelineSnapshot,
  PipelineStage, RuntimeInfo, StationSnapshot
} from './contracts'

type Guard<T> = (value: unknown) => value is T
type Dict = Record<string, unknown>
const isRecord = (value: unknown): value is Dict =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const isText = (value: unknown): value is string => typeof value === 'string'
const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)
const isNullableNumber = (value: unknown): value is number | null =>
  value === null || isFiniteNumber(value)
const isNullableText = (value: unknown): value is string | null =>
  value === null || isText(value)
const isNullableBool = (value: unknown): value is boolean | null =>
  value === null || typeof value === 'boolean'
const isArrayOf = <T>(value: unknown, guard: Guard<T>): value is T[] =>
  Array.isArray(value) && value.every(guard)
const qualityValues: ReadonlySet<string> = new Set(['GOOD','STALE','BAD','UNKNOWN'])
const executionValues: ReadonlySet<string> = new Set(['PENDING','SUCCEEDED','FAILED','UNKNOWN','REJECTED'])
const isQuality = (value: unknown): value is DataQuality => isText(value) && qualityValues.has(value)
const isExecution = (value: unknown): value is ExecutionState => isText(value) && executionValues.has(value)
const isTimestamp = (value: unknown): value is string =>
  isText(value) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
  Number.isFinite(Date.parse(value))
const isNullableTimestamp = (value: unknown): value is string | null =>
  value === null || isTimestamp(value)

const isMeasurement: Guard<Measurement> = (value): value is Measurement =>
  isRecord(value) && isNullableNumber(value.value) && isText(value.unit) &&
  isNullableTimestamp(value.timestamp) && isQuality(value.quality)

const isRuntimeInfo: Guard<RuntimeInfo> = (value): value is RuntimeInfo =>
  isRecord(value) && isText(value.controllerVersion) && isText(value.apiVersion) &&
  isNullableText(value.buildRevision)

const isStation: Guard<StationSnapshot> = (value): value is StationSnapshot => {
  if (!isRecord(value) || !isTimestamp(value.timestamp) || !isQuality(value.quality) ||
      !isNullableText(value.version) || !isNullableText(value.mode) ||
      !isNullableText(value.authority) || !isNullableText(value.strategy)) return false
  if (value.targetPower !== undefined && !isMeasurement(value.targetPower)) return false
  return ['activePower','reactivePower','voltage','frequency','ratedPower','availablePower',
    'periodMs','periodDeviationMs'].every(key => isMeasurement(value[key]))
}
const isControlFunction: Guard<ControlFunction> = (value): value is ControlFunction =>
  isRecord(value) && isText(value.id) && isText(value.name) && isNullableBool(value.enabled) &&
  isText(value.responseState) && isNullableText(value.algorithm) &&
  isNullableNumber(value.uptimeSeconds) && isMeasurement(value.target) &&
  isMeasurement(value.actual) && isNullableText(value.constraint)

const isCapabilityReason: Guard<CapabilityReason> = (value): value is CapabilityReason =>
  isRecord(value) && isText(value.deviceId) && isText(value.category) &&
  isText(value.reason) && isMeasurement(value.limitedPower) &&
  isNullableTimestamp(value.since)
const isCapability: Guard<CapabilitySnapshot> = (value): value is CapabilitySnapshot =>
  isRecord(value) && isText(value.id) && isText(value.label) &&
  isMeasurement(value.measurement) && isText(value.summary) &&
  isArrayOf(value.reasons,isCapabilityReason) && isTimestamp(value.timestamp)

const isDevice: Guard<DeviceSnapshot> = (value): value is DeviceSnapshot =>
  isRecord(value) && isText(value.id) && isText(value.type) &&
  isMeasurement(value.availablePower) && isMeasurement(value.activePower) &&
  isMeasurement(value.targetPower) && isNullableBool(value.participation) &&
  isText(value.status) && isNullableNumber(value.weight)

const isAuditEvent: Guard<AuditEvent> = (value): value is AuditEvent =>
  isRecord(value) && isText(value.id) && isTimestamp(value.timestamp) &&
  isText(value.kind) && isText(value.objectId) && isExecution(value.result) &&
  isNullableText(value.commandId) && isText(value.detail)

const isHistoryPoint: Guard<HistoryPoint> = (value): value is HistoryPoint =>
  isRecord(value) && isTimestamp(value.timestamp) &&
  isNullableNumber(value.value) && isQuality(value.quality)
const isHistorySeries: Guard<HistorySeries> = (value): value is HistorySeries =>
  isRecord(value) && isText(value.signalId) && isText(value.unit) &&
  isArrayOf(value.points,isHistoryPoint)

const isConfiguration: Guard<ControllerConfiguration> = (value): value is ControllerConfiguration =>
  isRecord(value) && isText(value.version) && isText(value.revision) &&
  isTimestamp(value.updatedAt) && isRecord(value.station) &&
  isRecord(value.controls) && isRecord(value.coordination)

const isConfigValidation: Guard<ConfigValidation> = (value): value is ConfigValidation =>
  isRecord(value) && typeof value.valid === 'boolean' &&
  isArrayOf(value.errors, (error: unknown): error is {path:string;message:string} =>
    isRecord(error) && isText(error.path) && isText(error.message))

const isOfflineExperiment: Guard<OfflineExperiment> = (value): value is OfflineExperiment =>
  isRecord(value) && isText(value.id) && isTimestamp(value.createdAt) &&
  isText(value.algorithmId) && isRecord(value.parameters) &&
  Object.values(value.parameters).every(isFiniteNumber) &&
  (value.status === 'DRAFT' || value.status === 'EVALUATED' || value.status === 'REJECTED') &&
  isRecord(value.metrics) && Object.values(value.metrics).every(isNullableNumber)

const isPipelineStage: Guard<PipelineStage> = (value): value is PipelineStage =>
  isRecord(value) && isText(value.id) && isText(value.label) &&
  isNullableText(value.algorithm) && isRecord(value.parameters) &&
  isArrayOf(value.inputs,isText) && isArrayOf(value.outputs,isText) &&
  (value.status === 'READY' || value.status === 'BLOCKED' || value.status === 'UNKNOWN')
const isPipeline: Guard<PipelineSnapshot> = (value): value is PipelineSnapshot =>
  isRecord(value) && isTimestamp(value.timestamp) && isQuality(value.quality) &&
  isArrayOf(value.stages,isPipelineStage)

const isCommandAudit: Guard<CommandAudit> = (value): value is CommandAudit =>
  isRecord(value) && isText(value.commandId) && isExecution(value.state) &&
  isTimestamp(value.timestamp) &&
  isArrayOf(value.deviceResults,
    (entry: unknown): entry is CommandAudit['deviceResults'][number] =>
      isRecord(entry) && isText(entry.deviceId) &&
      isExecution(entry.state) && isNullableText(entry.reason))

function decode<T>(value: unknown, guard: Guard<T>, description: string): T {
  if (!guard(value)) throw new TypeError(`Controller API ${description} 响应不符合数据契约`)
  return value
}

const arrayOf = <T>(guard: Guard<T>): Guard<T[]> =>
  (value: unknown): value is T[] => isArrayOf(value,guard)

export const decodeController = {
  runtimeInfo: (value: unknown) => decode(value,isRuntimeInfo,'运行版本'),
  station: (value: unknown) => decode(value,isStation,'场站快照'),
  functions: (value: unknown) => decode(value,arrayOf(isControlFunction),'控制功能'),
  capabilities: (value: unknown) => decode(value,arrayOf(isCapability),'可控能力'),
  devices: (value: unknown) => decode(value,arrayOf(isDevice),'设备列表'),
  events: (value: unknown) => decode(value,arrayOf(isAuditEvent),'审计事件'),
  history: (value: unknown) => decode(value,arrayOf(isHistorySeries),'历史信号'),
  configuration: (value: unknown) => decode(value,isConfiguration,'配置快照'),
  validation: (value: unknown) => decode(value,isConfigValidation,'配置校验'),
  experiments: (value: unknown) => decode(value,arrayOf(isOfflineExperiment),'离线试验'),
  pipeline: (value: unknown) => decode(value,isPipeline,'Pipeline 快照'),
  commandAudit: (value: unknown) => decode(value,isCommandAudit,'指令审计')
}
