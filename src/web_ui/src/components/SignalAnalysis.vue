<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseChart from './BaseChart.vue'
import type { EChartsOption } from 'echarts'
import type { HistorySeries } from '../api/contracts'
import { chartVisuals, chartAxisText, chartZoomText } from '../utils/chartTheme'
import { response } from '../mock/data'
import { welchPsd, type WelchResult } from '../utils/welch'

const props = withDefaults(defineProps<{ mock?: boolean; history?: HistorySeries[] }>(), {
  mock: true, history: () => []
})
type Channel = 'actual' | 'target' | 'total'
type Mode = 'time' | 'spectrum' | 'welch' | 'correlation'
const signalIds: Record<Channel, string> = {
  actual: 'station.active_power',
  target: 'station.active_power_target',
  total: 'station.devices_total_active_power'
}
const seriesMap: Record<Channel, string> = {
  actual: '实测有功', target: '目标有功', total: '设备合计有功'
}
const channel = ref<Channel>('actual')
const channelOptions = computed(() => (Object.entries(seriesMap) as [Channel,string][]).map(([id,label]) => ({
  id, label, disabled: !props.mock && !props.history.some(series => series.signalId === signalIds[id])
})))
const mode = ref<Mode>('time')
const windowType = ref<'hann' | 'rectangular'>('hann')
const removeMean = ref(true)
const segmentSize = ref(32)
const overlapFraction = ref(0.5)

type Sample = { timestamp: string; value: number }
function historySamples(signal: Channel): Sample[] {
  const source = props.history.find(series => series.signalId === signalIds[signal])
  if (!source) return []
  const qualified = source.points.filter(point => point.quality === 'GOOD' &&
    point.value !== null && Number.isFinite(point.value) && Number.isFinite(Date.parse(point.timestamp)))
  return qualified.map(point => ({ timestamp: point.timestamp, value: point.value! }))
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)).slice(-256)
}
const samples = computed<Sample[]>(() => props.mock
  ? response.map(row => ({ timestamp: row.time, value: row[channel.value] }))
  : historySamples(channel.value))

/** Reject irregular samples and missing data rather than silently interpolating measurements. */
const sampling = computed(() => {
  if (props.mock) return { valid: true, period: 2, reason: '' }
  if (samples.value.length < 4) return { valid: false, period: 0, reason: '有效样本不足 4 个' }
  const times = samples.value.map(point => Date.parse(point.timestamp))
  const intervals = times.slice(1).map((time, index) => (time - times[index]) / 1000)
  const sorted = [...intervals].sort((a,b) => a-b)
  const period = sorted[Math.floor(sorted.length / 2)]
  const tolerance = Math.max(period * 0.02, 0.005)
  const valid = period > 0 && intervals.every(delta => delta > 0 && Math.abs(delta-period) <= tolerance)
  return { valid, period, reason: valid ? '' : '信号时间戳间隔不均匀，禁止频域分析或相关计算' }
})
const raw = computed(() => samples.value.map(point => point.value))
const stats = computed(() => {
  const values = raw.value
  const n = values.length
  if (n === 0) return { n: 0, mean: null, rms: null, min: null, max: null }
  const mean = values.reduce((sum, value) => sum + value, 0) / n
  return { n, mean, rms: Math.sqrt(values.reduce((sum, value) => sum + value*value, 0)/n),
    min: Math.min(...values), max: Math.max(...values) }
})
const amplitude = computed(() => {
  if (!sampling.value.valid) return []
  const values = raw.value, n = values.length
  if (n < 4) return []
  const average = stats.value.mean ?? 0
  const window = values.map((_,index) => windowType.value === 'hann'
    ? 0.5 * (1 - Math.cos(2*Math.PI*index/(n-1))) : 1)
  const coherentGain = window.reduce((sum, weight) => sum + weight, 0)/n
  const input = values.map((value,index) => (value-(removeMean.value?average:0))*window[index])
  return Array.from({length:Math.floor(n/2)+1}, (_,frequencyIndex) => {
    let real = 0, imaginary = 0
    for(let i=0;i<n;i++){
      const phase = 2*Math.PI*frequencyIndex*i/n
      real += input[i]*Math.cos(phase)
      imaginary -= input[i]*Math.sin(phase)
    }
    const nyquist = n%2===0 && frequencyIndex===n/2
    const weight = frequencyIndex===0 || nyquist ? 1 : 2
    return { frequency: frequencyIndex/(n*sampling.value.period),
      amplitude: Math.hypot(real,imaginary)*weight/(n*coherentGain) }
  })
})
const welch = computed<WelchResult>(() => {
  if (!sampling.value.valid || raw.value.length < segmentSize.value)
    return { bins: [], segments: 0, resolutionHz: 0 }
  return welchPsd(raw.value, sampling.value.period, segmentSize.value,
    overlapFraction.value, windowType.value, removeMean.value)
})
const correlation = computed(() => {
  if (!sampling.value.valid) return []
  const targets = props.mock
    ? response.map(row => ({timestamp:row.time,value:row.target}))
    : historySamples('target')
  const byTime = new Map(targets.map(point => [point.timestamp, point.value]))
  const pairs = samples.value.filter(point => byTime.has(point.timestamp))
  if (pairs.length < 4 || !props.mock && pairs.length !== samples.value.length) return []
  const values = pairs.map(point => point.value)
  const reference = pairs.map(point => byTime.get(point.timestamp)!)
  const n = values.length
  const meanX = reference.reduce((sum,value)=>sum+value,0)/n
  const meanY = values.reduce((sum,value)=>sum+value,0)/n
  const maxLag = Math.min(20, Math.floor(n/3))
  return Array.from({length:maxLag*2+1}, (_,index) => {
    const lag = index-maxLag
    let numerator=0,energyX=0,energyY=0
    for(let i=0;i<n;i++){
      const j=i+lag
      if(j<0||j>=n) continue
      const x=reference[i]-meanX, y=values[j]-meanY
      numerator+=x*y
      energyX+=x*x
      energyY+=y*y
    }
    return {lag:lag*sampling.value.period,
      value:energyX>0&&energyY>0 ? numerator/Math.sqrt(energyX*energyY) : 0}
  })
})
const canAnalyze = computed(() => sampling.value.valid && stats.value.n >= 4)
const chartOption = computed<EChartsOption>(() => {
  let x: string[] = [], y: number[] = [], xName = '时间', yName = '功率 (MW)'
  if (mode.value==='spectrum') {
    x=amplitude.value.map(item=>item.frequency.toFixed(4))
    y=amplitude.value.map(item=>item.amplitude)
    xName='频率 (Hz)';yName='幅值 (MW)'
  } else if (mode.value==='welch') {
    x=welch.value.bins.map(item=>item.frequency.toFixed(4))
    y=welch.value.bins.map(item=>item.power)
    xName='频率 (Hz)';yName='PSD (MW²/Hz)'
  } else if (mode.value==='correlation') {
    x=correlation.value.map(item=>item.lag.toFixed(3))
    y=correlation.value.map(item=>item.value)
    xName='目标领先/滞后 (s)';yName='相关系数'
  } else {
    x=samples.value.map(item => props.mock ? item.timestamp : new Date(item.timestamp).toLocaleTimeString())
    y=raw.value
  }
  return {
    ...chartVisuals(), grid:{left:65,right:28,top:42,bottom:65},
    xAxis:{type:'category',name:xName,boundaryGap:false,data:x,...chartAxisText()},
    yAxis:{type:'value',name:yName,scale:mode.value!=='correlation',
      min:mode.value==='correlation'?-1:undefined,max:mode.value==='correlation'?1:undefined,...chartAxisText()},
    dataZoom:[{type:'inside'},{type:'slider',height:14,bottom:12,...chartZoomText()}],
    series:[{type:'line',name:seriesMap[channel.value],showSymbol:false,
      data:y.map(value=>Number(value.toFixed(5)))}]
  }
})
function formatMetric(value: number | null): string {
  return value === null ? '—' : value.toFixed(2)
}
</script>

<template>
  <el-card shadow="never" class="panel-card panel">
    <h2 class="app-panel-title panel-title">时域 · 幅度谱 · Welch PSD · 时滞相关</h2>
    <el-alert :title="mock ? '模拟波形，采样间隔 2 s；相关性不证明因果。' :
      '真实数据仅使用 GOOD 质量样本，不插值、不补点；必须满足均匀采样才能分析频域。'"
      type="info" :closable="false" show-icon />
    <el-alert v-if="!canAnalyze" :title="sampling.reason || '有效样本不足，无法计算分析结果'"
      type="warning" :closable="false" show-icon />
    <el-row :gutter="12" class="signal-tools">
      <el-col :xs="24" :sm="12" :lg="6">
        <el-select v-model="channel" aria-label="分析信号">
          <el-option v-for="option in channelOptions" :key="option.id" :label="option.label" :value="option.id" :disabled="option.disabled"/>
        </el-select>
      </el-col>
      <el-col :xs="24" :lg="12" class="signal-mode">
        <el-segmented v-model="mode" :options="[{label:'时域',value:'time'},{label:'幅度谱',value:'spectrum'},
          {label:'Welch PSD',value:'welch'},{label:'时滞相关',value:'correlation'}]"/>
      </el-col>
      <el-col v-if="mode==='spectrum'||mode==='welch'" :xs="24" :sm="12" :lg="6">
        <el-select v-model="windowType" aria-label="窗函数"><el-option label="Hann" value="hann"/><el-option label="矩形" value="rectangular"/></el-select>
      </el-col>
      <el-col v-if="mode==='spectrum'||mode==='welch'" :xs="24" :sm="12" :lg="6">
        <el-checkbox v-model="removeMean" label="去均值"/>
      </el-col>
      <el-col v-if="mode==='welch'" :xs="24" :sm="12" :lg="6">
        <el-select v-model="segmentSize" aria-label="Welch 分段长度"><el-option label="16 点" :value="16"/><el-option label="32 点" :value="32"/><el-option label="64 点" :value="64"/></el-select>
      </el-col>
      <el-col v-if="mode==='welch'" :xs="24" :sm="12" :lg="6">
        <el-select v-model="overlapFraction" aria-label="Welch 重叠率"><el-option label="0% 重叠" :value="0"/><el-option label="50% 重叠" :value="0.5"/><el-option label="75% 重叠" :value="0.75"/></el-select>
      </el-col>
    </el-row>
    <el-descriptions :column="2" border class="signal-stats">
      <el-descriptions-item label="样本量">{{stats.n}}</el-descriptions-item>
      <el-descriptions-item label="均值">{{formatMetric(stats.mean)}} MW</el-descriptions-item>
      <el-descriptions-item label="RMS">{{formatMetric(stats.rms)}} MW</el-descriptions-item>
      <el-descriptions-item label="峰峰值">{{formatMetric(stats.max===null||stats.min===null?null:stats.max-stats.min)}} MW</el-descriptions-item>
    </el-descriptions>
    <BaseChart v-if="mode==='time'||canAnalyze" :option="chartOption" height="380px" aria-label="时域或频域分析图" />
    <el-empty v-else description="时序质量不足，禁止输出伪造分析曲线" />
    <p class="section-note">频率分辨率 {{canAnalyze ? (mode==='welch'?welch.resolutionHz:1/(stats.n*sampling.period)).toFixed(5) : '—'}} Hz
      <span v-if="mode==='welch'"> · Welch 分段 {{welch.segments}} 组</span>
      · Nyquist 频率 {{canAnalyze ? (1/(2*sampling.period)).toFixed(3) : '—'}} Hz。试验评价须具备独立判据。</p>
  </el-card>
</template>
<style scoped>
.signal-tools{row-gap:var(--app-space-3);align-items:center;margin:var(--app-space-3) 0}
.signal-tools .el-select{width:100%;min-width:0}
.signal-mode{min-width:0}
.signal-mode :deep(.el-segmented){width:100%;max-width:100%;height:auto;flex-wrap:wrap}
.signal-stats{margin-bottom:var(--app-space-3)}
</style>
