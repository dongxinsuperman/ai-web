<template>
  <div class="wrap">
    <el-card>
      <template #header>运行配置（热调，立即对新任务生效）</template>
      <el-form label-width="160px" style="max-width: 760px">
        <el-form-item label="浏览器节点容量">
          <div class="node-table">
            <div class="node-head">
              <span>节点</span>
              <span v-for="b in browsers" :key="b.key">{{ b.label }}</span>
              <span>状态</span>
              <span></span>
            </div>
            <div class="node-row" v-for="node in nodeNames" :key="node">
              <span class="node-name">{{ node }}</span>
              <el-input-number
                v-for="b in browsers"
                :key="b.key"
                v-model="nodeSlots[node][b.key]"
                :min="0"
                :max="32"
                size="small"
              />
              <span class="status" :class="{ online: isOnline(node) }">
                {{ isOnline(node) ? "在线" : "离线" }}
              </span>
              <el-button link type="danger" @click="removeNode(node)">删除</el-button>
            </div>
            <div v-if="!nodeNames.length" class="empty-row">
              暂无 Agent。先启动 Agent，连接成功后会自动出现在这里。
            </div>
            <div class="agent-actions">
              <el-button size="small" :loading="loadingAgents" @click="refreshAgents">刷新在线 Agent</el-button>
              <span class="hint-inline">在线 Agent 会自动出现在表格里，无需手输节点名。</span>
            </div>
          </div>
          <div class="hint">
            每个引擎配几台 = 该引擎最多同时执行几个任务；0 = 不启用（提交该引擎会被拒）。
            总并发 = 各 Agent 节点各台数之和（当前 {{ total }}）。Server 只分发，浏览器由同名 Agent 执行。
          </div>
        </el-form-item>
        <el-form-item label="浏览器模式">
          <el-radio-group v-model="headless">
            <el-radio-button :label="false">有头 headful</el-radio-button>
            <el-radio-button :label="true">无头 headless</el-radio-button>
          </el-radio-group>
          <div class="hint">有头（headful）更不易被风控（如百度滑块），但需有显示器；无头适合服务器/Pod。切换对下一个任务生效。</div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          <el-button @click="load">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../lib/api";

const browsers = [
  { key: "chrome", label: "Chrome" },
  { key: "firefox", label: "Firefox" },
  { key: "webkit", label: "Safari" },
];
const nodeSlots = reactive({});
const agents = ref([]);
const headless = ref(false);
const saving = ref(false);
const loadingAgents = ref(false);

const nodeNames = computed(() => Object.keys(nodeSlots).sort((a, b) => a.localeCompare(b)));
const total = computed(() => nodeNames.value.reduce(
  (sum, node) => sum + browsers.reduce((s, b) => s + (Number(nodeSlots[node]?.[b.key]) || 0), 0),
  0,
));

function emptySlots() {
  return { chrome: 0, firefox: 0, webkit: 0 };
}

function normalizeSlots(raw) {
  const data = raw || {};
  const nested = Object.values(data).some((v) => v && typeof v === "object" && !Array.isArray(v));
  if (!nested) return {};
  const out = {};
  for (const [node, slots] of Object.entries(data)) {
    out[node] = { ...emptySlots(), ...(slots || {}) };
  }
  return out;
}

function replaceNodeSlots(next) {
  for (const key of Object.keys(nodeSlots)) delete nodeSlots[key];
  for (const [node, slots] of Object.entries(next)) {
    nodeSlots[node] = emptySlots();
    for (const b of browsers) nodeSlots[node][b.key] = Number(slots[b.key] ?? 0);
  }
}

function isOnline(node) {
  return agents.value.some((a) => a.agentId === node);
}

function ensureNode(node) {
  if (!nodeSlots[node]) nodeSlots[node] = emptySlots();
}

async function refreshAgents() {
  loadingAgents.value = true;
  try {
    agents.value = (await api.listAgents()).data.agents || [];
    for (const agent of agents.value) {
      const agentId = String(agent.agentId || "").trim();
      if (agentId) ensureNode(agentId);
    }
  } catch (e) {
    agents.value = [];
  } finally {
    loadingAgents.value = false;
  }
}

async function load() {
  const data = (await api.getConfig()).data;
  let parsed = {};
  try {
    parsed = typeof data.browser_slots === "string" ? JSON.parse(data.browser_slots) : (data.browser_slots || {});
  } catch (e) {
    parsed = {};
  }
  replaceNodeSlots(normalizeSlots(parsed));
  headless.value = String(data.headless).toLowerCase() === "true";
  await refreshAgents();
}

function removeNode(node) {
  delete nodeSlots[node];
}

async function save() {
  saving.value = true;
  try {
    const payload = {};
    for (const node of nodeNames.value) {
      payload[node] = {};
      for (const b of browsers) payload[node][b.key] = Number(nodeSlots[node]?.[b.key]) || 0;
    }
    await api.putConfig({ browser_slots: payload, headless: headless.value });
    ElMessage.success("已保存");
    load();
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.wrap { max-width: 900px; }
.hint { color: #9ca3af; font-size: 12px; margin-top: 4px; }
.node-table { width: 100%; display: grid; gap: 8px; }
.node-head,
.node-row { display: grid; grid-template-columns: 100px repeat(3, 120px) 72px 56px; gap: 8px; align-items: center; }
.node-head { color: #9ca3af; font-size: 12px; }
.node-name { color: #d1d5db; font-weight: 600; }
.status { color: #9ca3af; font-size: 12px; }
.status.online { color: #22c55e; }
.empty-row { color: #9ca3af; font-size: 12px; padding: 8px 0; }
.agent-actions { display: flex; gap: 8px; align-items: center; }
.hint-inline { color: #9ca3af; font-size: 12px; }
</style>
