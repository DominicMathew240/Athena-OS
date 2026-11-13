import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Relative base so GitHub Pages project URLs and local preview both work.
  base: "./",
});
