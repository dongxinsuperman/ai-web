<template>
  <div class="wrap">
    <el-card>
      <template #header>运行配置（热调，立即对新任务生效）</template>
      <el-form label-width="160px" style="max-width: 520px">
        <el-form-item label="浏览器槽位（台数）">
          <div class="slots">
            <div class="slot" v-for="b in browsers" :key="b.key">
              <span class="name">{{ b.label }}</span>
              <el-input-number v-model="slots[b.key]" :min="0" :max="32" size="small" />
            </div>
          </div>
          <div class="hint">
            每个引擎配几台 = 该引擎最多同时执行几个任务；0 = 不启用（提交该引擎会被拒）。
            总并发 = 各台数之和（当前 {{ total }}）。需先 playwright install 对应浏览器。
          </div>
        </el-form-item>
        <el-form-item label="浏览器模式">
          <el-switch v-model="headless" active-text="无头 headless" inactive-text="有头 headful" inline-prompt />
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
const slots = reactive({ chrome: 2, firefox: 1, webkit: 1 });
const headless = ref(false);
const saving = ref(false);

const total = computed(() => browsers.reduce((sum, b) => sum + (Number(slots[b.key]) || 0), 0));

async function load() {
  const data = (await api.getConfig()).data;
  let parsed = {};
  try {
    parsed = typeof data.browser_slots === "string" ? JSON.parse(data.browser_slots) : (data.browser_slots || {});
  } catch (e) {
    parsed = {};
  }
  for (const b of browsers) slots[b.key] = Number(parsed[b.key] ?? 0);
  headless.value = String(data.headless).toLowerCase() === "true";
}

async function save() {
  saving.value = true;
  try {
    const payload = {};
    for (const b of browsers) payload[b.key] = Number(slots[b.key]) || 0;
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
.wrap { max-width: 640px; }
.hint { color: #9ca3af; font-size: 12px; margin-top: 4px; }
.slots { display: flex; gap: 20px; flex-wrap: wrap; }
.slot { display: flex; align-items: center; gap: 8px; }
.slot .name { min-width: 52px; color: #d1d5db; }
</style>
