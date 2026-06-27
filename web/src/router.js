import { createRouter, createWebHashHistory } from "vue-router";
import Submissions from "./pages/Submissions.vue";
import Assets from "./pages/Assets.vue";
import Sites from "./pages/Sites.vue";
import Config from "./pages/Config.vue";

const routes = [
  { path: "/", redirect: "/submissions" },
  { path: "/submissions", component: Submissions, meta: { title: "队列 / 执行记录" } },
  { path: "/assets", component: Assets, meta: { title: "素材库" } },
  { path: "/sites", component: Sites, meta: { title: "站点 / 免登" } },
  { path: "/config", component: Config, meta: { title: "配置" } },
];

export default createRouter({ history: createWebHashHistory(), routes });
