<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseChart from './BaseChart.vue'
import type { EChartsOption } from 'echarts'
import type { HistorySeries } from '../api/contracts'
import { chartVisuals, chartAxisText } from '../utils/chartTheme'
import { response } from '../mock/data'

const props = withDefaults(defineProps<{ mock?: boolean; history?: HistorySeries[] }>(), {
  mock: true, history: () => []
})
type Channel = { name: string; unit: string; value: number | null; series: (number | null)[] }

const definitions = [
  { id: 'station.active_power', name: '全场有功', unit: 'MW' },
  { id: 'station.reactive_power', name: '全场无功', unit: 'Mvar' },
  { id: 'station.wind_speed', name: '平均风速', unit: 'm/s' },
  { id: 'station.wind_power', name: '风电有功', unit: 'MW' }
]
const timeline = computed(() => props.mock ? response.map(point => point.time) :
  [...new Set(props.history.flatMap(series => series.points.map(point => point.timestamp)))]
    .sort().slice(-180))

const channels = computed<Channel[]>(() => {
  if (props.mock) {
    const points = response.map((point, i) => ({
      p: point.actual, q: 12.4 + 1.8 * Math.sin(i / 11),
      wind: 7.8 + 1.1 * Math.sin(i / 17) + 0.35 * Math.sin(i / 5),
      windPower: 147 + 11 * Math.sin(i / 17) + 4 * Math.sin(i / 5)
    }))
    const sets = [
      points.map(point => point.p), points.map(point => point.q),
      points.map(point => point.wind), points.map(point => point.windPower)
    ]
    return definitions.map((definition, i) => ({
      name: definition.name, unit: definition.unit,
      value: sets[i][sets[i].length - 1] ?? null, series: sets[i]
    }))
  }
  return definitions.map(definition => {
    const input = props.history.find(series => series.signalId === definition.id)
    const byTime = new Map(input?.points.filter(point => point.quality === 'GOOD' &&
      point.value !== null && Number.isFinite(point.value)).map(point => [point.timestamp, point.value!]) ?? [])
    const values = timeline.value.map(time => byTime.get(time) ?? null)
    return { name: definition.name, unit: input?.unit ?? definition.unit,
      value: [...values].reverse().find(value => value !== null) ?? null, series: values }
  })
})
const selected = ref<string[]>((definitions.map(definition => definition.name)))
const chartHeight = computed(() => Math.max(240, selected.value.length * 148))
const chartOption = computed<EChartsOption>(() => {
  const data = channels.value.filter(item => selected.value.includes(item.name))
  if (!data.length) return { series: [] }
  const count = data.length
  const gap = 7
  const block = (88 - (count - 1) * gap) / count
  return {
    ...chartVisuals(),
    tooltip: { trigger: 'axis', textStyle: chartVisuals().textStyle },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { show: false },
    grid: data.map((_, index) => ({
      left: 64, right: 18, top: (5 + index * (block + gap)) + '%',
      height: block + '%', containLabel: true
    })),
    xAxis: data.map((_, index) => ({
      type: props.mock ? 'category' as const : 'time' as const,
      gridIndex: index,
      boundaryGap: false,
      ...(props.mock ? { data: timeline.value } : {}),
      axisLabel: { ...chartAxisText().axisLabel, show: index === count - 1 },
      name: index === count - 1 ? '时间' : '',
      nameTextStyle: chartAxisText().nameTextStyle
    })),
    yAxis: data.map((item, index) => ({
      type: 'value' as const, gridIndex: index, scale: true,
      name: item.name + ' (' + item.unit + ')', ...chartAxisText()
    })),
    dataZoom: [{ type: 'inside', xAxisIndex: data.map((_, index) => index) }],
    series: data.map((item, index) => ({
      name: item.name, type: 'line' as const, showSymbol: false,
      xAxisIndex: index, yAxisIndex: index,
      data: item.series.map((value, i) => value === null ? null : props.mock
        ? Number(value.toFixed(2)) : [timeline.value[i], Number(value.toFixed(2))])
    }))
  }
})
</script>

<template>
  <div class="station-trend">
    <div class="station-trend__heading">
      <h2 class="app-panel-title">全场运行仪表与同步趋势</h2>
      <span class="app-caption">{{ mock ? '模拟数据' : '仅显示 GOOD 质量历史测量' }} · 公共时间轴</span>
    </div>
    <el-row :gutter="12" class="station-trend__meters">
      <el-col v-for="channel in channels" :key="channel.name" :xs="12" :lg="6" class="station-trend__meter">
        <span class="metric-label">{{ channel.name }}</span>
        <strong class="app-metric">{{ channel.value === null ? '—' : channel.value.toFixed(2) }}
          <small>{{ channel.unit }}</small>
        </strong>
      </el-col>
    </el-row>
    <el-checkbox-group v-model="selected" class="station-trend__selector">
      <el-checkbox v-for="channel in channels" :key="channel.name" :value="channel.name" :label="channel.name" />
    </el-checkbox-group>
    <BaseChart :option="chartOption" :height="chartHeight + 'px'" aria-label="全场有功、无功、平均风速及风电有功同步趋势" />
  </div>
</template>

<style scoped>
.station-trend__heading{display:flex;justify-content:space-between;gap:var(--app-space-3);align-items:center;flex-wrap:wrap}
.station-trend__meters{row-gap:var(--app-space-3);margin:var(--app-space-4) 0}
.station-trend__meter{display:flex;flex-direction:column;gap:var(--app-space-2);min-width:0;overflow-wrap:anywhere}
.station-trend__meter small{font-size:var(--app-font-label)}
.station-trend__selector{display:flex;flex-wrap:wrap;gap:var(--app-space-3)}
</style>
