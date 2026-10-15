<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { usePipelineReadModel } from '../composables/useControllerReadModel'
import { isUsableState } from '../api/quality'

const props = defineProps<{ mock: boolean }>()

type Node = { id: string; label: string; algorithm: string; choices: string[]; inputs: string; outputs: string; parameters: Record<string, string>; constraints: string }
const nodes: Node[] = [
  { id:'reference', label:'Reference · 来源管理', algorithm:'PRIMARY_BACKUP', choices:['FIXED','PRIMARY_BACKUP','PRIORITY','EXPLICIT','COMPOSE'], inputs:'调度参考 / 内部参考 / 来源时间戳', outputs:'ControlReference', parameters:{primary:'GRID',backups:'THIRD_PARTY',invalid_action:'BLOCK'},constraints:'只使用当前获授权且具有一致物理语义的参考值' },
  { id:'preprocess',label:'测量预处理',algorithm:'IIR',choices:['DIRECT','MEDIAN','MOVING_AVERAGE','IIR','FIR'],inputs:'POI 数据 / 质量 / 时效',outputs:'ConditionedSnapshot',parameters:{time_constant_s:'0.5',sample_period_s:'0.2'},constraints:'过期/无效测量不得进入控制律' },
  { id:'trajectory',label:'轨迹生成',algorithm:'RATE_LIMIT',choices:['DIRECT','RATE_LIMIT','BUMPLESS'],inputs:'ControlReference / 当前目标',outputs:'ControlTrajectory',parameters:{rise_mw_s:'2',fall_mw_s:'3'},constraints:'PFR/IR 不得擅自继承 AGC 轨迹限制' },
  { id:'law',label:'场站控制律',algorithm:'FEEDFORWARD_PI',choices:['DIRECT','P','PI','PID','FEEDFORWARD_PI','MPC'],inputs:'轨迹 / POI 反馈 / 控制器状态',outputs:'PlantCandidateTarget',parameters:{kp:'0.8',ki:'0.12',anti_windup:'true'},constraints:'算法切换须在控制周期边界完成并保证安全' },
  { id:'coordination',label:'控制贡献协调',algorithm:'default',choices:['default','sum','priority','directional'],inputs:'AGC / PFR / IR 贡献与事件基准',outputs:'PlantControlTarget',parameters:{ir_override:'true',pfr_agc_mode:'same_direction_add',ir_weight:'1',pfr_weight:'1',agc_weight:'1'},constraints:'同一事件去重；各贡献统一增量基准及单位' },
  { id:'allocation',label:'设备分配 / P-Q 联合协调',algorithm:'HEADROOM',choices:['EQUAL','CAPACITY','HEADROOM','PRIORITY','QP'],inputs:'PlantControlTarget / 可控池 / P-Q 能力',outputs:'DeviceCommandPlan / residual',parameters:{reserve_mw:'1',priority:'available_headroom'},constraints:'不能超过设备容量、SOC 和 P/Q 联合可行域' },
  { id:'guard',label:'执行保护',algorithm:'FIXED_GUARD',choices:['FIXED_GUARD'],inputs:'计划 / 写权 / 控制代际 / 点元数据',outputs:'ControlOutcome',parameters:{writes_enabled:'false',retry_unknown_commands:'false'},constraints:'不可绕过；UNKNOWN 不自动重传；每条指令执行前校验控制权' }
]
const selectedId = ref('law')
const functionId = ref('active_power')
const live = usePipelineReadModel(functionId)
watch(functionId, () => { void live.refresh() })
const pipelineSnapshot = computed(() => live.data.value)
const pipelineUsable = computed(() => isUsableState(pipelineSnapshot.value) && (pipelineSnapshot.value?.stages.length ?? 0) > 0)
const nodeRows = computed<Node[]>(() => {
  if (!pipelineSnapshot.value || !pipelineUsable.value) return props.mock ? nodes : []
  return pipelineSnapshot.value.stages.map(stage => ({
    id: stage.id, label: stage.label, algorithm: stage.algorithm ?? 'UNKNOWN',
    choices: [stage.algorithm ?? 'UNKNOWN'], inputs: stage.inputs.join(' / '),
    outputs: stage.outputs.join(' / '),
    parameters: Object.fromEntries(Object.entries(stage.parameters).map(([k,v]) => [k,String(v)])),
    constraints: `实时状态：${stage.status} · 当前快照只读`
  }))
})
const node = computed(() => nodeRows.value.find(n => n.id === selectedId.value) ?? nodeRows.value[0])
const pipelineState = live.state
const pipelineError = live.error

const selection = ref<Record<string,string>>({})
const draftAlgorithm = computed({get:()=>selection.value[node.value.id] ?? node.value.algorithm,set:value=>{selection.value={...selection.value,[node.value.id]:value}}})
const candidates = ref<Record<string,Record<string,string>>>({})
const draftParams = computed(()=>candidates.value[node.value.id] ?? node.value.parameters)
function changeParameter(key:string,value:string) { candidates.value={...candidates.value,[node.value.id]:{...draftParams.value,[key]:value}} }
function resetDraft(){selection.value={...selection.value,[node.value.id]:node.value.algorithm};candidates.value={...candidates.value,[node.value.id]:{...node.value.parameters}}}
</script>

<template>
  <el-card shadow="never" class="panel-card panel pipeline-workbench">
    <div class="pipeline-header">
      <div><h2 class="app-panel-title">Controller 执行管线</h2><p class="section-note">按 v0_11 分层，点击节点检查算法、参数和输入输出。候选更改只保存在当前页面，不写入运行控制器。</p></div>
      <el-select v-model="functionId" aria-label="控制管线" style="width:160px">
        <el-option label="有功 P" value="active_power" />
        <el-option label="无功 Q" value="reactive_power" />
      </el-select>
    </div>
    <el-alert v-if="mock" title="离线 Pipeline 示意：不执行控制器算法、不写入设备。" type="info" :closable="false" show-icon />
    <el-alert v-else-if="pipelineState==='error'" :title="pipelineError ?? 'Pipeline 接口不可达'" type="error" :closable="false" show-icon />
    <el-alert v-else-if="!pipelineUsable" title="实时 Pipeline 数据尚未可用，运行状态为 UNKNOWN；不展示示意算法或参数。" type="warning" :closable="false" show-icon />
    <el-alert v-else title="当前为实时 Pipeline 只读快照；候选算法与参数修改仅存于浏览器，不会写入控制器。" type="info" :closable="false" show-icon />
    <el-row v-if="mock || pipelineUsable" :gutter="12" class="pipeline-layout">
      <el-col :xs="24" :lg="10"><div class="pipeline-nodes">
        <button v-for="(item,index) in nodeRows" :key="item.id" type="button" class="pipeline-node"
                :class="{'pipeline-node--active':selectedId===item.id}" @click="selectedId=item.id">
          <span class="pipeline-node__index">{{ index + 1 }}</span>
          <span class="pipeline-node__body"><strong>{{ item.label }}</strong><small>{{ selection[item.id] ?? item.algorithm }}</small></span>
          <span aria-hidden="true">›</span>
        </button>
      </div></el-col>
      <el-col :xs="24" :lg="14"><section class="pipeline-inspector" aria-label="节点检查器">
        <h3 class="app-section-title">{{ node.label }}</h3>
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="输入">{{ node.inputs }}</el-descriptions-item>
          <el-descriptions-item label="输出">{{ node.outputs }}</el-descriptions-item>
          <el-descriptions-item label="执行状态">{{mock ? '模拟 · 非实际执行' : (pipelineUsable ? '只读运行快照' : 'UNKNOWN')}}</el-descriptions-item>
        </el-descriptions>
        <el-form label-position="top" class="pipeline-form">
          <el-form-item label="候选算法（仅本地预览）">
            <el-select v-model="draftAlgorithm" :disabled="node.id==='guard' || !mock">
              <el-option v-for="item in node.choices" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item v-for="(value,key) in draftParams" :key="key" :label="String(key)">
            <el-input :model-value="value" :disabled="node.id==='guard' || !mock" @update:model-value="changeParameter(String(key),$event)" />
          </el-form-item>
        </el-form>
        <el-alert :title="node.constraints" type="warning" :closable="false" />
        <div class="pipeline-actions">
          <el-button :disabled="!mock" @click="resetDraft">重置候选更改</el-button>
          <el-button type="primary" disabled>应用到 Controller（未接入）</el-button>
        </div>
      </section></el-col>
    </el-row>
    <el-empty v-else description="暂无质量有效的 Controller Pipeline 快照" />
  </el-card>
</template>

<style scoped>
.pipeline-header{display:flex;justify-content:space-between;align-items:start;gap:var(--app-space-4);margin-bottom:var(--app-space-3)}
.pipeline-header p{margin:var(--app-space-2) 0 0}
.pipeline-layout{margin-top:var(--app-space-3);row-gap:var(--app-space-3)}
.pipeline-layout>.el-col{min-width:0}
.pipeline-nodes{display:flex;flex-direction:column;gap:var(--app-space-2)}
.pipeline-node{display:flex;align-items:center;gap:var(--app-space-3);text-align:left;background:var(--app-bg-surface);border:1px solid var(--app-border);border-radius:var(--app-control-radius);padding:var(--app-space-3);color:var(--app-text-primary);cursor:pointer;font:inherit;width:100%}
.pipeline-node:hover,.pipeline-node--active{border-color:var(--el-color-primary);background:var(--el-color-primary-light-9)}
.pipeline-node__index{font-weight:var(--app-font-weight-semibold);color:var(--app-text-secondary)}
.pipeline-node__body{flex:1;min-width:0;display:flex;flex-direction:column;gap:var(--app-space-1);overflow-wrap:anywhere}
.pipeline-node__body small{color:var(--app-text-secondary);font-size:var(--app-font-caption)}
.pipeline-inspector{min-width:0;border:1px solid var(--app-border-soft);border-radius:var(--app-panel-radius);padding:var(--app-space-4)}
.pipeline-inspector h3{margin-bottom:var(--app-space-3)}
.pipeline-form{margin-top:var(--app-space-3)}
.pipeline-actions{display:flex;justify-content:flex-end;gap:var(--app-space-2);margin-top:var(--app-space-3);flex-wrap:wrap}
@media(max-width:767px){.pipeline-header{flex-direction:column}}
</style>
