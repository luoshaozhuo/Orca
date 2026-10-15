<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'

const props = withDefaults(defineProps<{
  option: EChartsOption
  height?: string
  ariaLabel?: string
}>(), { height: '330px', ariaLabel: '交互图表' })

const element = ref<HTMLDivElement | null>(null)
let chart: ECharts | undefined
let observer: ResizeObserver | undefined
let disposed = false

function resize() {
  if (!chart || !element.value) return
  const { width, height } = element.value.getBoundingClientRect()
  // Element Plus drawers/tabs may temporarily give a chart no usable size.
  if (width > 0 && height > 0) chart.resize()
}

function update() {
  if (!chart) return
  chart.setOption(props.option, { notMerge: true, lazyUpdate: false })
}

onMounted(async () => {
  await nextTick()
  if (disposed || !element.value) return
  chart = echarts.init(element.value)
  update()
  observer = new ResizeObserver(resize)
  observer.observe(element.value)
  resize()
})

watch(() => props.option, update, { flush: 'post' })
watch(() => props.height, () => void nextTick(resize), { flush: 'post' })

onUnmounted(() => {
  disposed = true
  observer?.disconnect()
  chart?.dispose()
  chart = undefined
})
</script>

<template>
  <div ref="element" class="base-chart" :style="{ height }" role="img" :aria-label="ariaLabel" />
</template>

<style scoped>
.base-chart {
  display: block;
  width: 100%;
  min-width: 0;
  max-width: 100%;
}
</style>
