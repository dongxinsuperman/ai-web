<template>
  <div>
    <div class="bar">
      <el-upload :show-file-list="false" :http-request="doUpload">
        <el-button type="primary" :icon="Upload">上传素材</el-button>
      </el-upload>
      <el-button :icon="Refresh" @click="load">刷新</el-button>
    </div>
    <el-table :data="rows" v-loading="loading" border>
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="mime" label="类型" width="160" />
      <el-table-column label="大小" width="120">
        <template #default="{ row }">{{ (row.size / 1024).toFixed(1) }} KB</template>
      </el-table-column>
      <el-table-column prop="createdAt" label="上传时间" width="200" />
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button size="small" @click="open(row.url)">查看</el-button>
          <el-button size="small" type="danger" @click="remove(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { Upload, Refresh } from "@element-plus/icons-vue";
import { api } from "../lib/api";

const rows = ref([]);
const loading = ref(false);

async function load() {
  loading.value = true;
  try { rows.value = (await api.listAssets()).data; } finally { loading.value = false; }
}

async function doUpload(opt) {
  const form = new FormData();
  form.append("file", opt.file);
  try {
    await api.uploadAsset(form);
    ElMessage.success("上传成功");
    load();
  } catch (e) {
    ElMessage.error("上传失败：" + (e.response?.data?.detail || e.message));
  }
}

async function remove(id) {
  await api.deleteAsset(id);
  ElMessage.success("已删除");
  load();
}

const open = (url) => window.open(url, "_blank");
onMounted(load);
</script>

<style scoped>
.bar { margin-bottom: 14px; display: flex; gap: 10px; align-items: center; }
</style>
