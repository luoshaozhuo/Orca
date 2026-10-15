import { computed, onMounted, onUnmounted, ref, type Ref } from 'vue'
import { controllerApi, isApiConfigured } from '../api/client'
import { isUsableStation } from '../api/quality'
import type {
  AuditEvent, CapabilitySnapshot, ControlFunction, DeviceSnapshot, PipelineSnapshot,
  StationSnapshot
} from '../api/contracts'

type ResourceState = 'unconfigured' | 'loading' | 'ready' | 'error'
export interface Resource<T> {
  data: Ref<T | null>
  state: Ref<ResourceState>
  error: Ref<string | null>
  updatedAt: Ref<string | null>
  refresh: () => Promise<void>
}
export function useResource<T>(load: () => Promise<T>, periodMs = 0): Resource<T> {
  const data = ref<T | null>(null) as Ref<T | null>
  const state = ref<ResourceState>(isApiConfigured() ? 'loading' : 'unconfigured')
  const error = ref<string | null>(null)
  const updatedAt = ref<string | null>(null)
  let timer: ReturnType<typeof setInterval> | undefined
  let requestGeneration = 0
  let disposed = false
  let busy = false
  let pending = false

  async function refresh(): Promise<void> {
    if (!isApiConfigured()) { data.value = null; state.value = 'unconfigured'; return }
    if (disposed) return
    if (busy) { pending = true; return }
    busy = true
    const generation = ++requestGeneration
    if (data.value === null) state.value = 'loading'
    try {
      const result = await load()
      if (disposed || pending || generation !== requestGeneration) return
      data.value = result
      state.value = 'ready'
      error.value = null
      updatedAt.value = new Date().toISOString()
    } catch (cause) {
      if (disposed || pending || generation !== requestGeneration) return
      // Fail closed: a disconnected endpoint must never leave its last sample marked current.
      data.value = null
      state.value = 'error'
      error.value = cause instanceof Error ? cause.message : '数据获取失败'
    } finally {
      busy = false
      if (pending && !disposed) {
        pending = false
        void refresh()
      }
    }
  }
  onMounted(() => {
    void refresh()
    if (periodMs > 0) timer = setInterval(() => void refresh(), periodMs)
  })
  onUnmounted(() => {
    disposed = true
    requestGeneration++
    pending = false
    if (timer) clearInterval(timer)
  })
  return { data, state, error, updatedAt, refresh }
}
export function useStationReadModel() {
  const station = useResource<StationSnapshot>(controllerApi.station, 1000)
  const functions = useResource<ControlFunction[]>(controllerApi.functions, 1000)
  const capabilities = useResource<CapabilitySnapshot[]>(controllerApi.capabilities, 2000)
  const devices = useResource<DeviceSnapshot[]>(controllerApi.devices, 2000)
  const healthy = computed(() => station.state.value === 'ready' && isUsableStation(station.data.value))
  return { station, functions, capabilities, devices, healthy }
}
export function useEventReadModel() {
  return useResource<AuditEvent[]>(() => controllerApi.events(200), 3000)
}
export function usePipelineReadModel(functionId: Ref<string>) {
  return useResource<PipelineSnapshot>(() => controllerApi.pipeline(functionId.value), 2000)
}
