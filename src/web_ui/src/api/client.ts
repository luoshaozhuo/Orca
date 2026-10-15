import type {
  AuditEvent, CapabilitySnapshot, CommandAudit, ConfigValidation, ControllerConfiguration,
  ControlFunction, DeviceSnapshot, HistorySeries, OfflineExperiment, PipelineSnapshot,
  StationSnapshot
} from './contracts'

export class ApiError extends Error {
  constructor(public readonly status: number | null, message: string) { super(message); this.name = 'ApiError' }
}

function rootUrl(): string | null {
  const configured = String(import.meta.env.VITE_CONTROLLER_API_BASE_URL ?? '').trim()
  return configured ? configured.replace(/\/+$/, '') : null
}

export function isApiConfigured(): boolean { return rootUrl() !== null }

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const root = rootUrl()
  if (!root) throw new ApiError(null, '尚未配置 VITE_CONTROLLER_API_BASE_URL')
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 4000)
  try {
    const response = await fetch(`${root}${path}`, {
      ...options, signal: controller.signal, credentials: 'same-origin',
      cache: 'no-store', headers: { Accept: 'application/json', ...options.headers }
    })
    if (!response.ok) throw new ApiError(response.status, `Controller API HTTP ${response.status}`)
    return await response.json() as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(null, error instanceof Error ? error.message : 'Controller API 请求失败')
  } finally {
    clearTimeout(timeout)
  }
}
function query(params: Record<string, string | number | undefined>): string {
  const url = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined) url.set(key, String(value))
  const result = url.toString()
  return result ? `?${result}` : ''
}

/** Candidate read-side REST contract. Endpoints require backend implementation; no command writes. */
export const controllerApi = {
  station: () => request<StationSnapshot>('/v1/station/snapshot'),
  functions: () => request<ControlFunction[]>('/v1/control/functions'),
  capabilities: () => request<CapabilitySnapshot[]>('/v1/control/capabilities'),
  devices: () => request<DeviceSnapshot[]>('/v1/devices'),
  pipeline: (functionId = 'active_power') => request<PipelineSnapshot>(`/v1/control/pipeline${query({ function_id: functionId })}`),
  events: (limit = 200) => request<AuditEvent[]>(`/v1/events${query({ limit })}`),
  commandAudit: (id: string) => request<CommandAudit>(`/v1/commands/${encodeURIComponent(id)}/audit`),
  history: (signalIds: string[], from: string, to: string) => request<HistorySeries[]>(`/v1/history${query({ signals: signalIds.join(','), from, to })}`),
  configuration: () => request<ControllerConfiguration>('/v1/configuration'),
  validateConfiguration: (config: ControllerConfiguration) => request<ConfigValidation>('/v1/configuration/validate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config)
  }),
  experiments: () => request<OfflineExperiment[]>('/v1/engineering/experiments')
}
