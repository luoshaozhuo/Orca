<script setup lang="ts">
import { computed, ref } from 'vue'
import { events } from '../mock/data'
defineProps<{ mock: boolean }>()
const eventQuery = ref('')
const filteredEvents = computed(() => events.filter(event => Object.values(event).some(value =>
  String(value).toLowerCase().includes(eventQuery.value.trim().toLowerCase())
)))
</script>
<template>
 <div>

            <div class="panel-card panel"><h2 class="app-panel-title panel-title">事件、审计与操作追踪</h2>
              <div class="toolbar"><el-select disabled placeholder="事件类型" style="width:150px"/>
                <el-input v-model="eventQuery" :disabled="!mock" clearable placeholder="搜索指令 ID、机组或操作人" style="max-width:280px"/>
                <el-button disabled>导出审计</el-button></div>
              <el-table :data="mock?filteredEvents:[]" stripe style="margin-top:16px">
                <el-table-column prop="time" label="时间" min-width="175"/><el-table-column prop="kind" label="类型"/><el-table-column prop="object" label="对象"/>
                <el-table-column prop="result" label="执行结果"/><el-table-column prop="command" label="关联指令 ID" min-width="180"/><el-table-column prop="detail" label="详情" min-width="240"/>
              </el-table>
              <el-empty v-if="!mock" description="控制事件、配置变更及逐点执行记录接口待接入"/>
            </div>

 </div>
</template>
