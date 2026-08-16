import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "gallery", component: () => import("./views/GalleryView.vue") },
    { path: "/studio/:projectId?", name: "studio", component: () => import("./views/StudioView.vue") },
    { path: "/library", name: "library", component: () => import("./views/LibraryView.vue") },
    { path: "/settings", name: "settings", component: () => import("./views/SettingsView.vue") },
  ],
});
