<template>
  <div>
    <div class="bar">
      <el-button type="primary" :icon="Plus" @click="openCreate">新建站点</el-button>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
      <span class="hint">命中关键字 → 自动把网址注入模型；配了免登则执行时自动带登录态</span>
    </div>

    <el-table :data="rows" v-loading="loading" border>
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="keywords" label="关键字" min-width="160" />
      <el-table-column prop="url" label="网址" min-width="220" />
      <el-table-column label="免登" width="130">
        <template #default="{ row }"><el-tag :type="row.authType==='none'?'info':'success'">{{ authLabel(row.authType) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled?'success':'info'">{{ row.enabled?'是':'否' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="visible" :title="form.id?'编辑站点':'新建站点'" width="680px">
      <el-form label-width="100px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="如 课程系统" /></el-form-item>
        <el-form-item label="关键字">
          <el-input v-model="form.keywords" placeholder="多个用 / 、, 空格 分隔，如 课程系统／课程平台" />
        </el-form-item>
        <el-form-item label="网址"><el-input v-model="form.url" placeholder="https://..." /></el-form-item>
        <el-form-item label="免登方式">
          <el-select v-model="form.authType" style="width: 280px">
            <el-option label="无（正常登录/账号写任务里）" value="none" />
            <el-option label="登录态快照 storageState（静态，会过期）" value="storage_state" />
            <el-option label="Cookie 注入（静态）" value="cookies" />
            <el-option label="登录接口 login_api（动态现取，不过期·推荐）" value="login_api" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.authType==='login_api'" label="登录配方">
          <div style="width:100%">
            <div class="hint" style="margin-bottom:6px">
              填下面的「登录流程描述」，点【生成配方】——系统会自动产出配方并**真登录一次验证**，登得进去才回填到底部；登不进去说明这份配方不可用。
              （也可直接在底部粘一份配方、不填描述，点该按钮即对这份配方做登录验证。）「网址」请填登录后要进入的**前端页面**，不是登录接口。
            </div>
            <el-input v-model="descText" type="textarea" :rows="5" placeholder="登录流程描述（可串联多个接口）：
1) 访问 https://你的站点/api/login，提交账号密码；
2) 取上一步返回里的某个值，作为请求头/参数，访问 https://你的站点/api/exchange；
3) 从最后一个请求的返回里，拿到我们要用来鉴权的令牌；
最后把该令牌写入 cookie（或 localStorage）。" />
            <div style="margin:6px 0">
              <el-button type="primary" :loading="working" @click="buildOrTest">生成配方（自动登录验证）</el-button>
            </div>
            <el-input v-model="payloadText" type="textarea" :rows="8" placeholder='精确配方 JSON（recipe）。通用示例（单步登录）：
{
  "login_url": "https://你的站点/api/login",
  "method": "POST",
  "payload": {"username": "test", "password": "test"},
  "token_path": "data.token",
  "token_prefix": "Bearer ",
  "inject": {
    "cookies": [{"name": "token"}],
    "local_storage": [{"key": "token"}]
  }
}
需要二次换值的站点再加 "me": {"url":"...","send_headers":["Authorization"],"resp_header":"..."}' />
          </div>
        </el-form-item>

        <el-form-item v-if="form.authType==='storage_state'" label="登录态">
          <div style="width:100%">
            <div class="hint" style="margin:6px 0">手动粘贴 Playwright storageState JSON。推荐优先使用登录接口 login_api，运行时动态现取登录态。</div>
            <el-input v-model="payloadText" type="textarea" :rows="6" placeholder='storageState JSON，例如 {"cookies":[],"origins":[]}' />
          </div>
        </el-form-item>

        <el-form-item v-else-if="form.authType==='cookies'" label="Cookie">
          <el-input v-model="payloadText" type="textarea" :rows="6"
            placeholder='{"cookies":[{"name":"token","value":"...","domain":".example.com","path":"/"}]}' />
        </el-form-item>

        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible=false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Plus, Refresh } from "@element-plus/icons-vue";
import { api } from "../lib/api";

const rows = ref([]);
const loading = ref(false);
const visible = ref(false);
const saving = ref(false);
const working = ref(false);
const payloadText = ref("");
const descText = ref("");
const form = ref({ id: "", name: "", keywords: "", url: "", authType: "none", enabled: true });

const authLabel = (t) => ({ none: "无", storage_state: "登录态快照", cookies: "Cookie", login_api: "登录接口(动态)" }[t] || t);

async function load() {
  loading.value = true;
  try { rows.value = (await api.listSites()).data; } finally { loading.value = false; }
}

function openCreate() {
  form.value = { id: "", name: "", keywords: "", url: "", authType: "none", enabled: true };
  payloadText.value = "";
  descText.value = "";
  visible.value = true;
}

function openEdit(row) {
  form.value = { id: row.id, name: row.name, keywords: row.keywords, url: row.url, authType: row.authType, enabled: row.enabled };
  if (row.authType === "login_api") {
    payloadText.value = JSON.stringify((row.authPayload || {}).recipe || {}, null, 2);
    descText.value = (row.authPayload || {})._desc || "";  // 回填上次的自然语言描述
  } else {
    payloadText.value = row.authPayload && Object.keys(row.authPayload).length ? JSON.stringify(row.authPayload, null, 2) : "";
    descText.value = "";
  }
  visible.value = true;
}

async function buildOrTest() {
  if (!form.value.url) { ElMessage.warning("请先填站点网址（登录后要进入的前端页面）"); return; }
  const hasDesc = !!descText.value.trim();
  const hasRecipe = !!payloadText.value.trim();
  if (!hasDesc && !hasRecipe) { ElMessage.warning("请填写登录流程描述，或在底部粘一份配方"); return; }
  working.value = true;
  try {
    let r, title;
    if (hasDesc) {
      r = (await api.compileAuth(descText.value, form.value.url)).data;
      if (r.recipe) payloadText.value = JSON.stringify(r.recipe, null, 2);
      title = "生成配方（含登录验证）";
    } else {
      let recipe;
      try { recipe = JSON.parse(payloadText.value); }
      catch { ElMessage.error("配方不是合法 JSON"); working.value = false; return; }
      r = (await api.verifyAuth(recipe, form.value.url)).data;
      title = "配方登录验证";
    }
    const lines = [
      r.ok ? "✅ 能登进去，这份配方可用（请保存）" : "❌ " + (r.detail || "登不进去，这份配方暂不可用"),
      r.attempts ? `自动尝试 ${r.attempts} 次` : "",
      (r.cookies && r.cookies.length) ? `注入 cookie：[${r.cookies.join(", ")}]` : "",
      r.finalUrl ? `打开后落点：${r.finalUrl}` : "",
      r.title ? `页面标题：${r.title}` : "",
    ].filter(Boolean).join("\n");
    ElMessageBox.alert(lines, title, { type: r.ok ? "success" : "warning" });
  } catch (e) {
    ElMessage.error("处理失败：" + (e.response?.data?.detail || e.message));
  } finally {
    working.value = false;
  }
}

async function submit() {
  let authPayload = {};
  if (form.value.authType !== "none" && payloadText.value.trim()) {
    let parsed;
    try { parsed = JSON.parse(payloadText.value); }
    catch { ElMessage.error("免登配置不是合法 JSON"); return; }
    if (form.value.authType === "login_api") {
      authPayload = { recipe: parsed };
      if (descText.value.trim()) authPayload._desc = descText.value;  // 一并保存自然语言描述
    } else {
      authPayload = parsed;
    }
  }
  saving.value = true;
  try {
    const payload = {
      name: form.value.name, keywords: form.value.keywords, url: form.value.url,
      authType: form.value.authType, authPayload, enabled: form.value.enabled,
    };
    if (form.value.id) await api.updateSite(form.value.id, payload);
    else await api.createSite(payload);
    ElMessage.success("已保存");
    visible.value = false;
    load();
  } catch (e) {
    ElMessage.error("保存失败：" + (e.response?.data?.detail || e.message));
  } finally {
    saving.value = false;
  }
}

async function remove(row) {
  await api.deleteSite(row.id);
  ElMessage.success("已删除");
  load();
}

onMounted(load);
</script>

<style scoped>
.bar { margin-bottom: 14px; display: flex; gap: 10px; align-items: center; }
.hint { color: #9ca3af; font-size: 12px; }
</style>
