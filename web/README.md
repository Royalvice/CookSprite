# CookSprite Web

Vue 3 + TypeScript + Vite frontend for CookSprite. Pinia owns project/run state,
Vue Router provides Gallery/Workbench/Library/Settings routes, vue-i18n provides
Chinese and English, Phosphor supplies one icon family, and Three.js is loaded
only for the normal-light stage.

```bash
npm install
npm run dev
npm run build
npm test
```

Vite proxies `/api/v1` to `http://127.0.0.1:8000`. The browser never calls
ComfyUI and contains no inference or deterministic media processing. All
generation and extraction starts as a registered Action run.
