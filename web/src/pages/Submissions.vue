<template>
  <div class="queue-page">
    <div class="topbar">
      <div class="left">
        <h2>队列总览</h2>
        <span class="pill slot">并发 {{ snap.concurrency }}　执行中 {{ snap.running.length }}/{{ snap.concurrency }}</span>
        <span class="hint">每 2.5s 自动刷新</span>
        <span v-if="err" class="hint bad">{{ err }}</span>
      </div>
      <div class="right">
        <el-button type="primary" :icon="Plus" @click="openCreate">新建任务</el-button>
        <el-button :icon="Refresh" circle @click="load" />
      </div>
    </div>

    <div class="lanes">
      <!-- 排队中 -->
      <section class="lane">
        <header class="lane-head queued">
          <span class="dot" /> 排队中
          <span class="n">{{ snap.queued.length }}</span>
        </header>
        <div class="lane-body">
          <p v-if="!snap.queued.length" class="empty">— 队列空闲 —</p>
          <article v-for="(it, i) in snap.queued" :key="it.itemId" class="card q">
            <div class="row1">
              <span class="idx">#{{ i + 1 }}</span>
              <span class="title">{{ it.caseName || it.caseId }}</span>
            </div>
            <p class="rc">{{ it.runContent }}</p>
            <div class="row2"><span class="sid">批次 {{ short(it.submissionId) }}</span></div>
          </article>
        </div>
      </section>

      <!-- 执行中 -->
      <section class="lane">
        <header class="lane-head running">
          <span class="dot pulse" /> 执行中
          <span class="n">{{ snap.running.length }}</span>
        </header>
        <div class="lane-body">
          <p v-if="!snap.running.length" class="empty">— 暂无运行 —</p>
          <article v-for="it in snap.running" :key="it.itemId" class="card r">
            <div class="row1">
              <span class="live">● LIVE</span>
              <span class="title">{{ it.caseName || it.caseId }}</span>
              <span class="elapsed">{{ fmtSec(it.elapsedMs) }}</span>
            </div>
            <p class="rc">{{ it.runContent }}</p>
            <div class="row2">
              <span>第 {{ it.steps || 0 }} 步</span>
              <span class="sid">run {{ short(it.runId) }}</span>
              <span class="cancel" @click="cancelItem(it)">取消</span>
            </div>
          </article>
        </div>
      </section>

      <!-- 最近完成 -->
      <section class="lane">
        <header class="lane-head done">
          <span class="dot" /> 最近完成
          <span class="n">{{ snap.recent.length }}</span>
        </header>
        <div class="lane-body">
          <p v-if="!snap.recent.length" class="empty">— 暂无记录 —</p>
          <article v-for="it in snap.recent" :key="it.itemId" class="card d" :class="it.state">
            <div class="row1">
              <span class="state-pill" :class="it.state">{{ stateLabel(it.state) }}</span>
              <span class="title">{{ it.caseName || it.caseId }}</span>
            </div>
            <p class="rc">{{ it.runContent }}</p>
            <div class="row2">
              <span v-if="it.statusReason" class="reason">{{ it.statusReason }}</span>
              <a v-if="it.reportUrl" class="report" :href="it.reportUrl" target="_blank">查看报告 →</a>
            </div>
          </article>
        </div>
      </section>
    </div>

    <!-- 新建任务 -->
    <el-dialog v-model="createVisible" title="新建任务" width="600px">
      <el-form label-width="92px">
        <el-form-item label="批次名"><el-input v-model="form.name" placeholder="可选" /></el-form-item>
        <el-form-item label="caseId"><el-input v-model="form.caseId" placeholder="如 demo_001" /></el-form-item>
        <el-form-item label="自然语言目标">
          <el-input v-model="form.runContent" type="textarea" :rows="4"
            placeholder="如：打开 https://example.com 并验证标题包含 Example" />
        </el-form-item>
        <el-form-item label="批次参考">
          <el-input v-model="form.functionMapContext" type="textarea" :rows="3"
            placeholder="可选：整批共享的登录规则 / 测试账号 / 通用弹窗处理" />
        </el-form-item>
        <el-form-item label="任务参考">
          <el-input v-model="form.itemFunctionMapContext" type="textarea" :rows="3"
            placeholder="可选：仅当前 case 需要的入口、术语或异常处理" />
        </el-form-item>
        <el-form-item label="Webhook"><el-input v-model="form.callbackUrl" placeholder="可选回调 URL" /></el-form-item>
        <el-form-item label="素材">
          <el-select v-model="form.assets" multiple filterable placeholder="可选，引用素材库文件" style="width:100%">
            <el-option v-for="a in assets" :key="a.id" :label="a.name" :value="a.name" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">投递</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { Plus, Refresh } from "@element-plus/icons-vue";
import { api } from "../lib/api";

const snap = ref({ concurrency: 0, queued: [], running: [], recent: [] });
const assets = ref([]);
const err = ref("");
const createVisible = ref(false);
const submitting = ref(false);
const form = ref({
  name: "",
  caseId: "",
  runContent: "",
  functionMapContext: "",
  itemFunctionMapContext: "",
  callbackUrl: "",
  assets: [],
});
let timer = null;

const short = (s) => (s ? String(s).slice(0, 8) : "");
const fmtSec = (ms) => (ms == null ? "" : Math.floor(ms / 1000) + "s");
const stateLabel = (s) => ({ success: "成功", failed: "失败", cancelled: "已取消" }[s] || s);

async function load() {
  try {
    snap.value = (await api.getQueue()).data;
    err.value = "";
  } catch (e) {
    err.value = "加载失败：" + (e.response?.data?.detail || e.message);
  }
}

async function openCreate() {
  form.value = {
    name: "",
    caseId: "",
    runContent: "",
    functionMapContext: "",
    itemFunctionMapContext: "",
    callbackUrl: "",
    assets: [],
  };
  try { assets.value = (await api.listAssets()).data; } catch { assets.value = []; }
  createVisible.value = true;
}

async function submit() {
  if (!form.value.caseId || !form.value.runContent) {
    ElMessage.warning("caseId 与 自然语言目标 必填");
    return;
  }
  submitting.value = true;
  try {
    await api.createSubmission({
      submissionName: form.value.name || undefined,
      callbackUrl: form.value.callbackUrl || undefined,
      functionMapContext: form.value.functionMapContext || undefined,
      items: [{
        caseId: form.value.caseId,
        caseName: form.value.name || undefined,
        runContent: form.value.runContent,
        functionMapContext: form.value.itemFunctionMapContext || undefined,
        assets: form.value.assets,
      }],
    });
    ElMessage.success("已投递，进入队列");
    createVisible.value = false;
    load();
  } catch (e) {
    ElMessage.error("投递失败：" + JSON.stringify(e.response?.data?.detail || e.message));
  } finally {
    submitting.value = false;
  }
}

async function cancelItem(it) {
  try {
    await api.cancelItem(it.submissionId, it.caseId);
    ElMessage.success("已请求取消");
    load();
  } catch (e) {
    ElMessage.error("取消失败：" + (e.response?.data?.detail || e.message));
  }
}

onMounted(() => { load(); timer = setInterval(load, 2500); });
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.queue-page { display: flex; flex-direction: column; height: 100%; }
.topbar { display: flex; align-items: center; margin-bottom: 14px; }
.topbar .left { display: flex; align-items: baseline; gap: 12px; }
.topbar h2 { margin: 0; font-size: 18px; }
.topbar .right { margin-left: auto; display: flex; gap: 8px; }
.hint { font-size: 12px; color: #9ca3af; }
.hint.bad { color: #dc2626; }
.pill.slot { font-size: 12px; color: #1565c0; background: #e8f1fe; padding: 2px 10px; border-radius: 999px; }

.lanes { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.lane { display: flex; flex-direction: column; min-height: 0; background: #f1f5f9; border: 1px solid #e5e7eb; border-radius: 12px; overflow: hidden; }
.lane-head { display: flex; align-items: center; gap: 8px; padding: 12px 14px; font-weight: 600; font-size: 14px; background: #fff; border-bottom: 1px solid #eef2f7; }
.lane-head .n { margin-left: auto; background: #eef2f7; color: #475569; border-radius: 999px; padding: 0 9px; font-size: 12px; font-weight: 600; }
.lane-head .dot { width: 9px; height: 9px; border-radius: 50%; }
.lane-head.queued .dot { background: #94a3b8; }
.lane-head.running .dot { background: #2563eb; }
.lane-head.done .dot { background: #16a34a; }
.dot.pulse { animation: pulse 1.2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

.lane-body { flex: 1; min-height: 0; overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.empty { color: #9ca3af; font-size: 13px; text-align: center; margin: 24px 0; }

.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
.card.r { border-color: #c7d2fe; }
.card.d.success { border-left: 3px solid #16a34a; }
.card.d.failed { border-left: 3px solid #dc2626; }
.card.d.cancelled { border-left: 3px solid #94a3b8; }
.card .row1 { display: flex; align-items: center; gap: 8px; }
.card .title { font-weight: 600; font-size: 14px; color: #111827; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card .idx { color: #94a3b8; font-size: 13px; font-weight: 600; }
.card .rc { margin: 6px 0; font-size: 12.5px; color: #475569; line-height: 1.5; max-height: 3em; overflow: hidden; }
.card .row2 { display: flex; align-items: center; gap: 10px; font-size: 11px; color: #94a3b8; }
.card .sid { font-family: ui-monospace, Menlo, monospace; }
.card .elapsed { margin-left: auto; color: #2563eb; font-weight: 700; font-size: 13px; }
.live { color: #2563eb; font-size: 11px; font-weight: 700; letter-spacing: .04em; }
.cancel { margin-left: auto; color: #b45309; cursor: pointer; }
.cancel:hover { text-decoration: underline; }
.reason { color: #6b7280; }
.report { margin-left: auto; color: #2563eb; text-decoration: none; }
.report:hover { text-decoration: underline; }

.state-pill { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.state-pill.success { background: #dcfce7; color: #166534; }
.state-pill.failed { background: #fee2e2; color: #991b1b; }
.state-pill.cancelled { background: #e5e7eb; color: #374151; }
</style>
