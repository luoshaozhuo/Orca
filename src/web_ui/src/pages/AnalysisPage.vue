<script setup lang="ts">
import { computed, ref } from 'vue'
import ResponseChart from '../components/ResponseChart.vue'
import SignalAnalysis from '../components/SignalAnalysis.vue'
import { response, tests, reasons } from '../mock/data'
import { controllerApi } from '../api/client'
import { useResource } from '../composables/useControllerReadModel'
import type { AssessmentResult, DiagnosticFinding, HistorySeries, IdentificationResult } from '../api/contracts'
const props = defineProps<{ mock: boolean }>()
const analysisTab = ref('performance')
const assessments = useResource(controllerApi.assessments)
const identifications = useResource(controllerApi.identifications)
const diagnostics = useResource(controllerApi.diagnostics)
const assessmentState = assessments.state
const assessmentError = assessments.error
const identificationState = identifications.state
const identificationError = identifications.error
const diagnosticState = diagnostics.state
const diagnosticError = diagnostics.error

const assessmentRows = computed(() => props.mock
  ? [
      {id:'AGC-demo',name:'AGC 跟踪偏差',basis:'项目 AGC 要求',status:'待评价',evidence:'—'},
      {id:'PFR-demo',name:'PFR 响应时间',basis:'批准的一次调频试验方案',status:'待评价',evidence:'—'},
      {id:'FREQ-demo',name:'测频精度',basis:'适用测频条款',status:'待评价',evidence:'—'}
    ]
  : (assessments.data.value ?? []).map((item: AssessmentResult) => ({
      id:item.id,name:item.name,basis:`${item.standard} · ${item.criterion}`,
      status:item.status,evidence:item.evidenceIds.join('、') || '—'
    }))
)
const identificationRows = computed(() => (identifications.data.value ?? []).map((item: IdentificationResult) => ({
  id:item.id,model:item.modelFamily,status:item.status,
  fit:item.fitScore === null ? '—' : item.fitScore.toFixed(3),
  validationSet:item.validationSetId ?? '—',createdAt:item.createdAt
})))
const diagnosticRows = computed(() => props.mock ? reasons :
  (diagnostics.data.value ?? []).map((item: DiagnosticFinding) => ({
    name:item.name,contribution:item.quality==='GOOD' ? item.contribution : 'UNKNOWN',
    evidence:item.quality==='GOOD' ? (item.evidenceIds.join('、') || '无证据') : '质量无效',
    detail:item.detail
  }))
)
const fromTime = ref(new Date(Date.now() - 60 * 60 * 1000).toISOString())
const toTime = ref(new Date().toISOString())
const liveHistory = useResource<HistorySeries[]>(() => controllerApi.history(['station.active_power','station.active_power_target','station.devices_total_active_power'], fromTime.value, toTime.value), 0)
const historyState = liveHistory.state
const historyError = liveHistory.error
const historySeries = computed(() => liveHistory.data.value ?? [])
const historyRows = computed(() => {
  const actual = liveHistory.data.value?.find(series => series.signalId === 'station.active_power')
  const target = liveHistory.data.value?.find(series => series.signalId === 'station.active_power_target')
  if (!actual || !target) return []
  const targets = new Map(target.points.filter(point => point.quality === 'GOOD' && point.value !== null)
    .map(point => [point.timestamp, point.value as number]))
  return actual.points.filter(point => point.quality === 'GOOD' && point.value !== null && targets.has(point.timestamp))
    .map(point => ({
      time: point.timestamp,
      target: targets.get(point.timestamp)!,
      actual: point.value!,
      total: point.value!,
      limit: 0
    }))
})
async function refreshHistory() {
  const now = Date.now()
  toTime.value = new Date(now).toISOString()
  fromTime.value = new Date(now - 3600000).toISOString()
  await liveHistory.refresh()
}

</script>

<template>
            <el-tabs v-model="analysisTab">
              <el-tab-pane label="动态性能" name="performance" />
              <el-tab-pane label="标准符合性" name="compliance" />
              <el-tab-pane label="信号与频域分析" name="signal" />
              <el-tab-pane label="系统辨识" name="identification" />
              <el-tab-pane label="寻因分析" name="diagnosis" />
            </el-tabs>
            <el-card shadow="never" class="panel-card panel" v-if="analysisTab==='performance'">
              <h2 class="app-panel-title panel-title">动态性能与响应评价</h2>
              <ResponseChart v-if="mock || historyRows.length" :data="mock ? response : historyRows" :show-limit="mock" :preview="mock" :title="mock ? 'AGC 阶跃 · 目标 / 响应（示意）' : '全场有功历史 · 目标 / 反馈'" />
              <el-alert v-if="!mock && historyState==='error'" type="error" :title="historyError ?? '历史数据接口失败'" :closable="false" />
              <el-empty v-else-if="!mock && !historyRows.length" description="无质量有效且时间对齐的历史响应数据" />
              <el-button v-if="!mock" :loading="historyState==='loading'" @click="refreshHistory">查询最近一小时历史</el-button>
              <el-table :data="mock ? tests : []" stripe>
                <el-table-column prop="id" label="试验" />
                <el-table-column prop="algorithm" label="算法" />
                <el-table-column prop="settle" label="调节时间 (s)" />
                <el-table-column prop="overshoot" label="超调 (%)" />
                <el-table-column prop="error" label="稳态误差 (MW)" />
              </el-table>
            </el-card>
            <el-card shadow="never" v-else-if="analysisTab==='compliance'" class="panel-card panel">
              <h2 class="app-panel-title panel-title">标准符合性与试验证据</h2>
              <el-alert title="先配置适用标准、项目条款、测量边界和批准判据；证据不足不自动判定合格。" type="warning" :closable="false" show-icon />
              <el-alert v-if="!mock && assessmentState==='error'" type="error" :title="assessmentError ?? '标准评价数据不可用'" :closable="false" />
              <el-table v-if="assessmentRows.length" :data="assessmentRows" stripe>
                <el-table-column prop="name" label="指标" min-width="120" />
                <el-table-column prop="basis" label="评价依据" min-width="200" />
                <el-table-column prop="status" label="结果" min-width="130">
                  <template #default="{row}">
                    <el-tag :type="row.status==='PASS'?'success':row.status==='FAIL'?'danger':'info'">{{row.status}}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="evidence" label="证据编号" min-width="160" />
              </el-table>
              <el-empty v-else description="尚未取得带有效证据的标准评价记录" />
              <el-button v-if="!mock" :loading="assessmentState==='loading'" @click="assessments.refresh">读取评价记录</el-button>
            </el-card>
            <SignalAnalysis v-else-if="analysisTab==='signal' && (mock || historySeries.length)" :mock="mock" :history="historySeries" />
            <el-card shadow="never" v-else-if="analysisTab==='signal'" class="panel-card panel"><el-empty description="等待包含原始时间戳及质量标记的历史数据" /><el-button @click="refreshHistory">重新查询</el-button></el-card>
            <el-card shadow="never" v-else-if="analysisTab==='identification'" class="panel-card panel">
              <h2 class="app-panel-title panel-title">系统辨识</h2>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="输入">参考目标 / 注入扰动</el-descriptions-item>
                <el-descriptions-item label="输出">PCC 有功、无功反馈</el-descriptions-item>
                <el-descriptions-item label="模型">一阶惯性滞后 / 二阶模型 / 状态空间 / FRF</el-descriptions-item>
                <el-descriptions-item label="校核">独立数据集拟合与残差检查</el-descriptions-item>
              </el-descriptions>
              <el-alert v-if="!mock && identificationState==='error'" type="error" :title="identificationError ?? '辨识记录不可用'" :closable="false" />
              <el-table v-if="!mock && identificationRows.length" :data="identificationRows" stripe>
                <el-table-column prop="id" label="模型 ID" min-width="120" />
                <el-table-column prop="model" label="模型类型" min-width="140" />
                <el-table-column prop="status" label="验证状态" min-width="115" />
                <el-table-column prop="fit" label="拟合指标" min-width="100" />
                <el-table-column prop="validationSet" label="验证集" min-width="110" />
                <el-table-column prop="createdAt" label="创建时间" min-width="160" />
              </el-table>
              <el-empty v-if="!mock && !identificationRows.length" description="暂无独立验证的系统辨识模型" />
              <el-button v-if="!mock" :loading="identificationState==='loading'" @click="identifications.refresh">读取模型记录</el-button>
              <el-button disabled>开始辨识（后端待接入）</el-button>
            </el-card>
            <el-card shadow="never" v-else class="panel-card panel">
              <h2 class="app-panel-title panel-title">寻因分析与证据</h2>
              <el-alert title="相关性不能替代因果验证：须保留统一时间轴、控制阶段输出和设备执行证据。" type="info" :closable="false" />
              <el-alert v-if="!mock && diagnosticState==='error'" type="error" :title="diagnosticError ?? '诊断数据不可用'" :closable="false" />
              <el-table v-if="diagnosticRows.length" :data="diagnosticRows" stripe>
                <el-table-column prop="name" label="候选因素" min-width="150" />
                <el-table-column prop="contribution" label="初步影响" min-width="130" />
                <el-table-column prop="evidence" label="关联证据" min-width="180" />
                <el-table-column prop="detail" label="分析说明" min-width="180" />
              </el-table>
              <el-empty v-else description="尚无可用的候选寻因证据" />
              <el-button v-if="!mock" :loading="diagnosticState==='loading'" @click="diagnostics.refresh">读取分析记录</el-button>
            </el-card>
</template>
