import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // GitHub Pages serves from /PikaFlow/ on the gh-pages branch
  base: '/PikaFlow/',
  build: {
    outDir: 'dist',
  },
})

