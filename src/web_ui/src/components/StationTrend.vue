<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'
import { response } from '../mock/data'

type Channel = { name: string; unit: string; value: number; series: number[] }
const channels = computed<Channel[]>(() => {
  const values = response.map((p, i) => {
    const wind = 7.8 + 1.1 * Math.sin(i / 17) + 0.35 * Math.sin(i / 5)
    return {
      p: p.actual,
      q: 12.4 + 1.8 * Math.sin(i / 11),
      wind,
      windPower: 147 + 11 * Math.sin(i / 17) + 4 * Math.sin(i / 5)
    }
  })
  const last = values[values.length - 1]
  return [
    { name: '全场有功', unit: 'MW', value: last.p, series: values.map(v => v.p) },
    { name: '全场无功', unit: 'Mvar', value: last.q, series: values.map(v => v.q) },
    { name: '平均风速', unit: 'm/s', value: last.wind, series: values.map(v => v.wind) },
    { name: '风电有功', unit: 'MW', value: last.windPower, series: values.map(v => v.windPower) }
  ]
})
const selected = ref<string[]>(['全场有功', '全场无功', '平均风速', '风电有功'])
const container = ref<HTMLDivElement | null>(null)
let chart: ECharts | undefined
let resizeObserver: ResizeObserver | undefined

function draw() {
  if (!chart) return
  const data = channels.value.filter(channel => selected.value.includes(channel.name))
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { show: false },
    grid: { top: 28, left: 110, right: 125, bottom: 45 },
    xAxis: { type: 'category', data: response.map(point => point.time), boundaryGap: false },
    yAxis: data.map((channel, index) => ({
      type: 'value', name: channel.unit, scale: true,
      position: index % 2 === 0 ? 'left' : 'right',
      offset: Math.floor(index / 2) * 55,
      axisLine: { show: false }, splitLine: { show: index === 0 }
    })),
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 5 }],
    series: data.map((channel, index) => ({
      name: channel.name, type: 'line', showSymbol: false,
      yAxisIndex: index, data: channel.series.map(n => Number(n.toFixed(2)))
    }))
  }, true)
}
onMounted(() => {
  if (!container.value) return
  chart = echarts.init(container.value)
  draw()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(container.value)
})
watch(selected, draw)
onUnmounted(() => { resizeObserver?.disconnect(); chart?.dispose() })
</script>

<template>
  <div class="station-trend">
    <div class="station-trend__heading">
      <h2 class="app-panel-title">全场运行仪表与同步趋势</h2>
      <span class="app-caption">模拟数据 · 公共时间轴</span>
    </div>
    <div class="station-trend__meters">
      <div v-for="c in channels" :key="c.name" class="station-trend__meter">
        <span class="metric-label">{{ c.name }}</span>
        <strong class="app-metric">{{ c.value.toFixed(2) }} <small>{{ c.unit }}</small></strong>
      </div>
    </div>
    <el-checkbox-group v-model="selected" class="station-trend__selector">
      <el-checkbox v-for="c in channels" :key="c.name" :value="c.name" :label="c.name" />
    </el-checkbox-group>
    <div ref="container" class="station-trend__chart" role="img" aria-label="全场有功、无功、平均风速及风电有功同步趋势" />
  </div>
</template>

<style scoped>
.station-trend__heading{display:flex;justify-content:space-between;gap:var(--app-space-3);align-items:center}
.station-trend__meters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--app-space-3);margin:var(--app-space-4) 0}
.station-trend__meter{display:flex;flex-direction:column;gap:var(--app-space-2);min-width:0}
.station-trend__meter small{font-size:var(--app-font-label)}
.station-trend__selector{display:flex;flex-wrap:wrap;gap:var(--app-space-3)}
.station-trend__chart{width:100%;height:350px;min-width:0}
@media(max-width:767px){.station-trend__meters{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
