import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        floating: resolve(__dirname, "floating.html"),
      },
    },
  },
  server: {
    port: 1420,
    strictPort: true,
  },
});

