<script setup lang="ts">
import { computed, ref } from 'vue'
import MetricCard from '../components/MetricCard.vue'
import StationTrend from '../components/StationTrend.vue'
import { capabilityMetrics, type CapabilityMetric } from '../mock/capabilities'
import { station, controls } from '../mock/data'
import { useStationReadModel } from '../composables/useControllerReadModel'
import type { CapabilitySnapshot, Measurement } from '../api/contracts'
import { isUsableMeasurement } from '../api/quality'

type Health = 'healthy' | 'unavailable' | 'unknown'
type Service = { id: string; name: string; url: string; state: Health; detail: string; checkedAt?: string }
const props = defineProps<{ mock: boolean; services: Service[] }>()
const live = useStationReadModel()
const stationSnapshot = computed(() => live.station.data.value)
const stationHealthy = live.healthy
function display(value: Measurement | undefined, digits: number): string {
  return isUsableMeasurement(value)
    ? value.value.toFixed(digits) : '—'
}
const liveFunctions = computed(() => (live.functions.data.value ?? []).map(f => ({
 name:f.name, enabled:f.enabled === null?'未知':f.enabled?'已投入':'已退出', response:f.responseState,
 algorithm:f.algorithm ?? '—', uptime:f.uptimeSeconds === null?'—':`${Math.round(f.uptimeSeconds/60)} min`
})))
const renderedCapabilities = computed<CapabilityMetric[]>(() => props.mock ? capabilityMetrics :
  (live.capabilities.data.value ?? []).map((capability: CapabilitySnapshot) => ({
    id: capability.id,
    label: capability.label,
    value: display(capability.measurement, 2),
    unit: capability.measurement.unit,
    summary: capability.summary,
    definition: `快照：${capability.timestamp}；质量：${capability.measurement.quality}`,
    details: capability.reasons.map(reason => ({
      id: reason.deviceId,
      type: reason.category,
      reason: reason.reason,
      capacity: isUsableMeasurement(reason.limitedPower) ? reason.limitedPower.value ?? undefined : undefined,
      since: reason.since ?? '—'
    }))
  })))
)
const selectedMetric = ref<CapabilityMetric | null>(null)
const metricDrawerOpen = ref(false)
const openMetric = (metric: CapabilityMetric) => { selectedMetric.value = metric; metricDrawerOpen.value = true }
const statusLabel = (state: Health) => state === 'healthy' ? '可达' : state === 'unavailable' ? '不可达' : '未检测'
const statusType = (state: Health) => state === 'healthy' ? 'success' : state === 'unavailable' ? 'danger' : 'info'
</script>
<template>
 <div>

            <el-row :gutter="12" class="overview-metrics">
              <el-col v-for="m in [
                {label:'并网点有功',value:mock?station.active.toFixed(1):display(stationSnapshot?.activePower,1),unit:'MW',summary:mock?'负荷率 62.2% · 模拟':'实际测量待接入',definition:'并网点有功功率，采用带质量标记的最新有效测量，单位 MW。'},
                {label:'并网点无功',value:mock?station.reactive.toFixed(1):display(stationSnapshot?.reactivePower,1),unit:'Mvar',summary:mock?'全场并网点实测 · 模拟':'实际测量待接入',definition:'并网点无功功率，按场站统一的无功正负方向约定显示。'},
                {label:'并网点电压',value:mock?station.voltage.toFixed(2):display(stationSnapshot?.voltage,2),unit:'kV',summary:mock?'35 kV 母线 · 模拟':'实际测量待接入',definition:'并网点电压有效测量值；计量位置及线电压/相电压口径由配置确定。'},
                {label:'系统频率',value:mock?station.frequency.toFixed(2):display(stationSnapshot?.frequency,2),unit:'Hz',summary:mock?'额定 50 Hz · 模拟':'实际测量待接入',definition:'控制所采用的频率测量，须带原始时标、有效性和数据质量状态。'}
              ]" :key="m.label" :xs="24" :sm="12" :lg="6"><MetricCard v-bind="m" /></el-col>
            </el-row>
            <el-card shadow="never" class="panel-card panel overview-mode">
              <div><span class="metric-label">当前控制模式</span><strong>{{ mock ? '远方自动 · AGC + PFR' : (stationHealthy ? stationSnapshot?.mode ?? '未知' : '未知 · 数据无效') }}</strong></div>
              <div><span class="metric-label">控制权</span><strong>{{ mock ? station.controlAuthority : (stationHealthy ? stationSnapshot?.authority ?? '未知' : '未知') }}</strong></div>
              <div><span class="metric-label">执行策略</span><strong>{{ mock ? station.strategy : (stationHealthy ? stationSnapshot?.strategy ?? '未知' : '未知') }}</strong></div>
            </el-card>
            <h2 class="app-panel-title panel-title">全场可控能力</h2>
            <el-empty v-if="!mock && !renderedCapabilities.length" description="可控能力快照暂不可用，不推断设备健康或可控容量" />
            <el-row :gutter="12" class="overview-metrics">
              <el-col v-for="metric in renderedCapabilities" :key="metric.id" :xs="24" :sm="12" :md="8" :xl="4" class="capability-col"><MetricCard
                :label="metric.label" :value="metric.value" :unit="metric.unit"
                :summary="metric.summary"
                :definition="metric.definition" :clickable="metric.details.length > 0" :warning="metric.details.length > 0"
                @open="openMetric(metric)" /></el-col>
            </el-row>
            <el-row :gutter="12" class="overview-rows">
              <el-col :xs="24" :lg="14">
                <el-card shadow="never" class="panel-card panel">
                  <h2 class="app-panel-title panel-title">控制功能与算法</h2>
                  <el-table :data="mock?controls:liveFunctions" stripe>
                    <el-table-column prop="name" label="功能" width="90"/>
                    <el-table-column prop="enabled" label="投入状态"/>
                    <el-table-column prop="response" label="响应状态"/>
                    <el-table-column prop="algorithm" label="当前算法"/>
                    <el-table-column prop="uptime" label="运行时长"/>
                  </el-table>
                  <p class="section-note">功能投入与功能响应分开显示；待接入 Controller 运行状态。</p>
                </el-card>
              </el-col>
              <el-col :xs="24" :lg="10">
                <el-card shadow="never" class="panel-card panel">
                  <h2 class="app-panel-title panel-title">上位机及 Wind Hub 通信</h2>
                  <div class="status-row"><span>上位机 / 调度指令源</span><el-tag type="info">运行状态待接入</el-tag></div>
                  <div v-for="service in services" :key="service.id" class="status-row">
                    <span>{{service.name}}</span>
                    <el-tag :type="statusType(service.state)">{{statusLabel(service.state)}}</el-tag>
                  </div>
                  <p class="section-note">服务可达性不代表业务健康。数据时效、订阅质量及写入能力需后台独立上报。</p>

                </el-card>
              </el-col>
            </el-row>
            <el-row :gutter="12" class="overview-rows">
              <el-col :xs="24" :lg="14">
                <el-card shadow="never" class="panel-card panel">
                  <h2 class="app-panel-title panel-title">实时性能与总体配置</h2>
                  <el-descriptions :column="2" border>
                    <el-descriptions-item label="控制周期设定">{{mock?station.period:display(stationSnapshot?.periodMs,0)}} ms</el-descriptions-item>
                    <el-descriptions-item label="控制周期偏差">{{mock?station.periodDeviation:display(stationSnapshot?.periodDeviationMs,1)}} ms</el-descriptions-item>
                    <el-descriptions-item label="协调策略">{{mock?station.strategy:(stationHealthy ? stationSnapshot?.strategy ?? '—' : '—')}}</el-descriptions-item>
                    <el-descriptions-item label="配置版本">{{mock?station.version:stationSnapshot?.version ?? '—'}}</el-descriptions-item>
                    <el-descriptions-item label="控制权限">{{mock?station.controlAuthority:(stationHealthy ? stationSnapshot?.authority ?? '未知':'未知')}}</el-descriptions-item>
                    <el-descriptions-item label="最后有效测量">{{mock?station.lastSample:stationSnapshot?.timestamp ?? '—'}}</el-descriptions-item>
                  </el-descriptions>
                </el-card>
              </el-col>
              <el-col :xs="24" :lg="10">
                <el-card shadow="never" class="panel-card panel">
                  <h2 class="app-panel-title panel-title">运行概览</h2>
                  <div class="status-row"><span>全场额定容量</span><b>{{mock?station.rated:display(stationSnapshot?.ratedPower,1)}} MW</b></div>
                  <div class="status-row"><span>可用调节容量</span><b>{{mock?station.available:display(stationSnapshot?.availablePower,1)}} MW</b></div>
                  <div class="status-row"><span>控制执行异常</span><b>{{mock?'1 项待确认':'—'}}</b></div>
                  <div class="status-row"><span>最近控制事件</span><b>{{mock?'14:41:12 AGC 调节':'—'}}</b></div>
                </el-card>
              </el-col>
            </el-row>
            <el-drawer v-model="metricDrawerOpen" direction="rtl" size="680px" class="capability-drawer"
                       :title="selectedMetric?.label + ' · 详细原因'">
              <template v-if="selectedMetric">
                <el-descriptions :column="1" border>
                  <el-descriptions-item label="当前指标">{{ selectedMetric.value }} {{ selectedMetric.unit }}</el-descriptions-item>
                  <el-descriptions-item label="异常摘要">{{ selectedMetric.summary }}</el-descriptions-item>
                  <el-descriptions-item label="数据说明">{{mock ? '模拟数据 · 不代表实际控制能力' : 'Controller 能力快照 · 仅质量有效的数值参与统计'}}</el-descriptions-item>
                </el-descriptions>
                <h3 class="app-section-title metric-drawer-section">受影响设备</h3>
                <el-table :data="selectedMetric.details" max-height="440" stripe>
                  <el-table-column prop="id" label="设备编号" min-width="110" sortable />
                  <el-table-column prop="type" label="设备类型" min-width="95" />
                  <el-table-column prop="reason" label="原因" min-width="125" />
                  <el-table-column prop="capacity" label="受限容量" min-width="95">
                    <template #default="{row}">{{ row.capacity != null ? row.capacity + (mock ? '（示意）' : '') : '—' }}</template>
                  </el-table-column>
                  <el-table-column prop="since" label="发生时间" min-width="90" />
                </el-table>
                <p class="section-note">同一设备可能存在多个原因；正式接口需提供主原因、附加原因和快照时间，避免重复统计。</p>
              </template>
            </el-drawer>
            <el-card shadow="never" class="panel-card panel overview-trend"><StationTrend v-if="mock"/><el-empty v-else description="等待全场 P/Q、风速及风电有功趋势数据"/></el-card>

 </div>
</template>
