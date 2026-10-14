<script setup lang="ts">
import { ref } from 'vue'
import { station, controls, units } from '../mock/data'
defineProps<{ mock: boolean }>()
const selectedConfig = ref('station')
</script>
<template>
  <div>

            <el-tabs v-model="selectedConfig">
              <el-tab-pane label="全场配置" name="station">
                <div class="content-grid">
                  <div class="panel-card panel">
                    <h2 class="app-panel-title panel-title">电厂与控制配置</h2>
                    <el-tabs>
                      <el-tab-pane label="电厂参数"><el-descriptions v-if="mock" border :column="2"><el-descriptions-item label="电站名称">{{station.name}}</el-descriptions-item><el-descriptions-item label="额定容量">{{station.rated}} MW</el-descriptions-item><el-descriptions-item label="控制周期">{{station.period}} ms</el-descriptions-item><el-descriptions-item label="可调容量">{{station.available}} MW</el-descriptions-item></el-descriptions><el-empty v-else description="并网点、额定容量、运行约束：后端配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="控制功能"><el-table v-if="mock" :data="controls"><el-table-column prop="name" label="控制功能"/><el-table-column prop="enabled" label="状态"/><el-table-column prop="algorithm" label="算法"/></el-table><el-empty v-else description="控制功能配置接口待接入"/></el-tab-pane>
                      <el-tab-pane label="协调策略"><el-descriptions v-if="mock" border><el-descriptions-item label="合成策略">default</el-descriptions-item><el-descriptions-item label="IR 优先级">1</el-descriptions-item><el-descriptions-item label="PFR 优先级">2</el-descriptions-item><el-descriptions-item label="AGC 优先级">3</el-descriptions-item></el-descriptions><el-empty v-else description="协调策略配置待接入"/></el-tab-pane>
                    </el-tabs>
                  </div>
                  <div class="panel-card panel"><h2 class="app-panel-title panel-title">接入与配置管理</h2>
                    <div class="status-row"><span>上位机 / 调度接口</span><el-tag :type="mock?'success':'info'">{{mock?'已配置 · 演示':'待配置'}}</el-tag></div>
                    <div class="status-row"><span>Commander gRPC</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>消息中间件及订阅</span><el-tag type="info">待配置</el-tag></div>
                    <div class="status-row"><span>配置版本及生效状态</span><b>{{mock?station.version+' · 生效中':'—'}}</b></div>
                    <div class="toolbar" style="margin-top:16px">
                      <el-button disabled>导入配置</el-button><el-button disabled>导出配置</el-button>
                      <el-button type="primary" disabled>校验并应用</el-button>
                    </div>
                    <p class="section-note">加载、校验、保存、应用分离；接口未接入时不能虚报成功。</p>
                  </div>
                </div>
              </el-tab-pane>
              <el-tab-pane label="机组配置" name="units">
                <div class="panel-card panel"><h2 class="app-panel-title panel-title">机组与资源管理</h2>
                  <div class="toolbar"><el-input placeholder="搜索机组名称或编号" disabled style="max-width:260px"/>
                    <el-select disabled placeholder="设备类型" style="width:150px"/></div>
                  <el-table :data="mock?units:[]" stripe style="margin-top:16px">
                    <el-table-column prop="id" label="设备编号"/><el-table-column prop="type" label="类型"/><el-table-column prop="available" label="可用容量 (MW)"/>
                    <el-table-column label="控制参与"><template #default="scope"><el-tag :type="scope.row.enabled?'success':'info'">{{scope.row.enabled?'参与':'退出'}}</el-tag></template></el-table-column><el-table-column prop="status" label="运行状态"/><el-table-column prop="weight" label="控制权重"/>
                  </el-table>
                  <el-empty v-if="!mock" description="待对接 Wind Hub 设备与测点元数据"/>
                </div>
              </el-tab-pane>
            </el-tabs>

  </div>
</template>
