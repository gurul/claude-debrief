import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Memory viewer for the debrief/ folder. Lives INSIDE debrief/ so the host
// repo's existing `debrief/` gitignore rule covers it — never commit your
// memory. Markdown is imported at dev time via
// import.meta.glob('../..*'), so the viewer hot-reloads as memory files
// change on disk.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5199,
    strictPort: true,
    fs: {
      // Allow importing the markdown that lives one level above the
      // viewer root (debrief/*.md, debrief/sessions/**).
      allow: ['..'],
    },
  },
});
