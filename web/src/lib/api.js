import axios from "axios";

// 开发态走 Vite 代理（baseURL 留空）；如需直连后端可设 VITE_API_BASE。
const baseURL = import.meta.env.VITE_API_BASE || "";
const token = import.meta.env.VITE_API_TOKEN || "";

const http = axios.create({ baseURL });
http.interceptors.request.use((config) => {
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export const api = {
  getQueue: () => http.get("/api/queue"),
  listSubmissions: () => http.get("/api/submissions"),
  getSubmission: (id) => http.get(`/api/submissions/${id}`),
  getItem: (id, caseId) =>
    http.get(`/api/submissions/${id}/items/${caseId}`, { params: { include_run: true } }),
  createSubmission: (payload) => http.post("/api/submissions", payload),
  cancelSubmission: (id) => http.post(`/api/submissions/${id}/cancel`),
  cancelItem: (id, caseId) => http.post(`/api/submissions/${id}/cases/${caseId}/cancel`),
  listAssets: () => http.get("/api/assets"),
  uploadAsset: (form) => http.post("/api/assets", form),
  deleteAsset: (assetId) => http.delete(`/api/assets/${assetId}`),
  getConfig: () => http.get("/api/config"),
  putConfig: (payload) => http.put("/api/config", payload),
  listAgents: () => http.get("/api/browser-agents"),
  listSites: () => http.get("/api/sites"),
  createSite: (payload) => http.post("/api/sites", payload),
  updateSite: (id, payload) => http.put(`/api/sites/${id}`, payload),
  deleteSite: (id) => http.delete(`/api/sites/${id}`),
  recordStart: (url) => http.post("/api/sites/record/start", { url }),
  recordSave: (recordToken) => http.post("/api/sites/record/save", { recordToken }),
  recordCancel: (recordToken) => http.post("/api/sites/record/cancel", { recordToken }),
  parseAuth: (description) => http.post("/api/sites/parse-auth", { description }),
  compileAuth: (description, url) => http.post("/api/sites/compile-auth", { description, url }),
  verifyAuth: (recipe, url) => http.post("/api/sites/verify-auth", { recipe, url }),
};

export default http;
