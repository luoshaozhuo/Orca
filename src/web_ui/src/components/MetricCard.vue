<script setup lang="ts">
import { InfoFilled } from '@element-plus/icons-vue'

withDefaults(defineProps<{
  label: string
  value: string
  unit?: string
  summary?: string
  definition: string
  clickable?: boolean
  warning?: boolean
}>(), { unit: '', summary: '', clickable: false, warning: false })

const emit = defineEmits<{ open: [] }>()
</script>

<template>
  <div class="panel-card metric-card" :class="{ 'metric-card--clickable': clickable }"
       :role="clickable ? 'button' : undefined" :tabindex="clickable ? 0 : undefined"
       @click="clickable && emit('open')"
       @keydown.enter.prevent="clickable && emit('open')"
       @keydown.space.prevent="clickable && emit('open')">
    <div class="metric-card__heading">
      <span class="metric-label">{{ label }}</span>
      <el-tooltip :content="definition" placement="top" :show-after="200" :enterable="true" :popper-options="{ strategy: 'fixed' }">
        <span class="metric-card__info" role="button" tabindex="0" :aria-label="label + '：指标定义'"
              @click.stop @keydown.enter.stop @keydown.space.stop>
          <el-icon><InfoFilled /></el-icon>
        </span>
      </el-tooltip>
    </div>
    <div class="app-metric metric-value">{{ value }} <small v-if="unit">{{ unit }}</small></div>
    <div class="metric-foot" :class="{ 'metric-card__warning': warning }">{{ summary || ' ' }}</div>
  </div>
</template>
