<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption, ECharts } from 'echarts'

const props = withDefaults(defineProps<{
  option: EChartsOption
  height?: string
  ariaLabel?: string
}>(), { height: '330px', ariaLabel: '交互图表' })

const element = ref<HTMLDivElement | null>(null)
let chart: ECharts | undefined
let observer: ResizeObserver | undefined

function update() {
  if (!chart) return
  chart.setOption(props.option, { notMerge: true, lazyUpdate: false })
  chart.resize()
}
onMounted(async () => {
  await nextTick()
  if (!element.value) return
  chart = echarts.init(element.value)
  update()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value)
})
watch(() => props.option, update, { deep: false, flush: 'post' })
watch(() => props.height, () => void nextTick(() => chart?.resize()), { flush: 'post' })
onUnmounted(() => { observer?.disconnect(); chart?.dispose(); chart = undefined })
</script>
<template>
  <div ref="element" class="base-chart" :style="{ height }" role="img" :aria-label="ariaLabel" />
</template>
<style scoped>
.base-chart{width:100%;min-width:0;display:block}
</style>
