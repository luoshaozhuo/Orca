<script setup lang="ts">
import { ref } from 'vue'
import ResponseChart from '../components/ResponseChart.vue'
import SignalAnalysis from '../components/SignalAnalysis.vue'
import { response, tests, reasons } from '../mock/data'
defineProps<{ mock: boolean }>()
const analysisTab = ref('performance')
</script>

<template>
            <el-tabs v-model="analysisTab">
              <el-tab-pane label="动态性能" name="performance" />
              <el-tab-pane label="标准符合性" name="compliance" />
              <el-tab-pane label="信号与频域分析" name="signal" />
              <el-tab-pane label="系统辨识" name="identification" />
              <el-tab-pane label="寻因分析" name="diagnosis" />
            </el-tabs>
            <div class="panel-card panel" v-if="analysisTab==='performance'">
              <h2 class="app-panel-title panel-title">动态性能与响应评价</h2>
              <ResponseChart v-if="mock" :data="response" title="AGC 阶跃 · 目标 / 响应" />
              <el-table :data="mock ? tests : []" stripe>
                <el-table-column prop="id" label="试验" />
                <el-table-column prop="algorithm" label="算法" />
                <el-table-column prop="settle" label="调节时间 (s)" />
                <el-table-column prop="overshoot" label="超调 (%)" />
                <el-table-column prop="error" label="稳态误差 (MW)" />
              </el-table>
            </div>
            <div v-else-if="analysisTab==='compliance'" class="panel-card panel">
              <h2 class="app-panel-title panel-title">标准符合性与试验证据</h2>
              <el-alert title="先配置适用标准、项目条款、测量边界和批准判据；证据不足不自动判定合格。" type="warning" :closable="false" show-icon />
              <el-table :data="[{name:'AGC 跟踪偏差',basis:'项目 AGC 要求',status:'待评价'},{name:'PFR 响应与调节时间',basis:'批准的一次调频试验方案',status:'待评价'},{name:'测频精度',basis:'适用测频条款',status:'待评价'}]">
                <el-table-column prop="name" label="指标" /><el-table-column prop="basis" label="评价依据" /><el-table-column prop="status" label="结果" />
              </el-table>
            </div>
            <SignalAnalysis v-else-if="analysisTab==='signal' && mock" />
            <div v-else-if="analysisTab==='signal'" class="panel-card panel"><el-empty description="等待同步历史信号与采样率数据" /></div>
            <div v-else-if="analysisTab==='identification'" class="panel-card panel">
              <h2 class="app-panel-title panel-title">系统辨识</h2>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="输入">参考目标 / 注入扰动</el-descriptions-item>
                <el-descriptions-item label="输出">PCC 有功、无功反馈</el-descriptions-item>
                <el-descriptions-item label="模型">一阶惯性滞后 / 二阶模型 / 状态空间 / FRF</el-descriptions-item>
                <el-descriptions-item label="校核">独立数据集拟合与残差检查</el-descriptions-item>
              </el-descriptions>
              <el-button disabled>开始辨识（后端待接入）</el-button>
            </div>
            <div v-else class="panel-card panel">
              <h2 class="app-panel-title panel-title">寻因分析与证据</h2>
              <el-alert title="相关性不能替代因果验证：须保留统一时间轴、控制阶段输出和设备执行证据。" type="info" :closable="false" />
              <el-table :data="mock ? reasons : []">
                <el-table-column prop="name" label="候选因素" /><el-table-column prop="contribution" label="初步影响" /><el-table-column prop="evidence" label="证据示意" />
              </el-table>
            </div>
</template>
