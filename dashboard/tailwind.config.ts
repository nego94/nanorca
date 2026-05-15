import type { Config } from "tailwindcss";

export default {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          bg:      "#0f1117",
          card:    "#1a1d26",
          border:  "#2a2d3a",
          green:   "#22c55e",
          red:     "#ef4444",
          yellow:  "#eab308",
          blue:    "#3b82f6",
          purple:  "#a855f7",
          muted:   "#6b7280",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
